"""Auto-log for every MCP tool call — persists queries and responses.

Every tool invocation is recorded with timestamp, tool name, arguments,
response text, and response size. This creates a complete research audit
trail that survives across Claude sessions.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..config import Settings

logger = logging.getLogger("research-mcp-server")

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    response_text TEXT NOT NULL,
    response_size INTEGER NOT NULL,
    is_error INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_timestamp ON tool_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_name ON tool_calls(tool_name);
"""


class ResearchHistory:
    """Auto-logs every MCP tool call for research audit trail.

    Stores at {storage_path}/research_history.db.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            settings = Settings()
            db_path = settings.STORAGE_PATH / "research_history.db"
        self.db_path = db_path
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create tables if needed."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.commit()
        self._initialized = True

    async def log_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        response_text: str,
        is_error: bool = False,
        duration_ms: Optional[int] = None,
    ) -> int:
        """Log a tool call.

        Args:
            tool_name: MCP tool name.
            arguments: Tool input arguments.
            response_text: Full response text.
            is_error: Whether the response is an error.
            duration_ms: Execution time in milliseconds.

        Returns:
            Row ID of the logged entry.
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO tool_calls
                   (timestamp, tool_name, arguments, response_text, response_size, is_error, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    tool_name,
                    json.dumps(arguments, default=str),
                    response_text,
                    len(response_text),
                    1 if is_error else 0,
                    duration_ms,
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_history(
        self,
        *,
        tool_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get tool call history.

        Args:
            tool_name: Filter by tool name.
            limit: Max results.
            offset: Skip first N.
            errors_only: Only return error responses.

        Returns:
            List of tool call records.
        """
        await self._ensure_initialized()
        conditions: list[str] = []
        params: list[Any] = []

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if errors_only:
            conditions.append("is_error = 1")

        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"""SELECT id, timestamp, tool_name, arguments,
                    response_size, is_error, duration_ms
                    FROM tool_calls WHERE {where}
                    ORDER BY timestamp DESC LIMIT ? OFFSET ?"""
        params.extend([limit, offset])

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                r = dict(row)
                r["arguments"] = json.loads(r["arguments"])
                results.append(r)
            return results

    async def get_call(self, call_id: int) -> Optional[dict[str, Any]]:
        """Get a single tool call with full response text."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tool_calls WHERE id = ?", (call_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            r = dict(row)
            r["arguments"] = json.loads(r["arguments"])
            return r

    async def get_stats(self) -> dict[str, Any]:
        """Get history statistics."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            total = (
                await (await db.execute("SELECT COUNT(*) FROM tool_calls")).fetchone()
            )[0]
            errors = (
                await (
                    await db.execute("SELECT COUNT(*) FROM tool_calls WHERE is_error = 1")
                ).fetchone()
            )[0]
            by_tool = {}
            cursor = await db.execute(
                "SELECT tool_name, COUNT(*) as c FROM tool_calls GROUP BY tool_name ORDER BY c DESC"
            )
            for row in await cursor.fetchall():
                by_tool[row[0]] = row[1]

            # Recent activity (last 24h, 7d, 30d)
            cursor = await db.execute(
                """SELECT
                    SUM(CASE WHEN timestamp > datetime('now', '-1 day') THEN 1 ELSE 0 END) as last_24h,
                    SUM(CASE WHEN timestamp > datetime('now', '-7 days') THEN 1 ELSE 0 END) as last_7d,
                    SUM(CASE WHEN timestamp > datetime('now', '-30 days') THEN 1 ELSE 0 END) as last_30d
                FROM tool_calls"""
            )
            activity = await cursor.fetchone()

        return {
            "total_calls": total,
            "total_errors": errors,
            "by_tool": by_tool,
            "activity": {
                "last_24h": activity[0] or 0,
                "last_7d": activity[1] or 0,
                "last_30d": activity[2] or 0,
            },
        }

    async def search_history(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search history by arguments or response content."""
        await self._ensure_initialized()
        pattern = f"%{query}%"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, timestamp, tool_name, arguments,
                   response_size, is_error, duration_ms
                   FROM tool_calls
                   WHERE arguments LIKE ? OR response_text LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (pattern, pattern, limit),
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                r = dict(row)
                r["arguments"] = json.loads(r["arguments"])
                results.append(r)
            return results
