"""Engram-pattern persistent research memory store.

Tracks research sessions, thesis evolution, session papers, and
session digests across Claude sessions. Provides warm context for
continuity between research runs.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..config import Settings

logger = logging.getLogger("research-mcp-server")

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_papers (
    session_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'read',
    notes TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (session_id, paper_id),
    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
);

CREATE TABLE IF NOT EXISTS thesis_tracker (
    id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'exploratory',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 0.5,
    first_proposed TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS session_digests (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    created_at TEXT NOT NULL,
    content TEXT NOT NULL,
    validated_theses TEXT NOT NULL DEFAULT '[]',
    invalidated_theses TEXT NOT NULL DEFAULT '[]',
    emerging_patterns TEXT NOT NULL DEFAULT '[]',
    active_opportunities TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON research_sessions(status);
CREATE INDEX IF NOT EXISTS idx_thesis_status ON thesis_tracker(status);
CREATE INDEX IF NOT EXISTS idx_digests_created ON session_digests(created_at);
"""


class ResearchMemory:
    """Persistent research memory with session, thesis, and digest tracking.

    Stores at {storage_path}/research_memory.db.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            settings = Settings()
            db_path = settings.STORAGE_PATH / "research_memory.db"
        self.db_path = db_path
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create tables if they don't exist yet."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.commit()
        self._initialized = True

    # ── Sessions ──────────────────────────────────────────────────────

    async def create_session(self, name: str, goal: str) -> str:
        """Create a new research session.

        Args:
            name: Session name.
            goal: Research goal description.

        Returns:
            UUID of the created session.
        """
        await self._ensure_initialized()
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO research_sessions (id, name, goal, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', ?, ?)""",
                (session_id, name, goal, now, now),
            )
            await db.commit()
        return session_id

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """Get a session by ID.

        Args:
            session_id: UUID of the session.

        Returns:
            Session dict or None.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM research_sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_sessions(
        self, status: Optional[str] = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """List research sessions.

        Args:
            status: Filter by status (e.g., 'active', 'closed').
            limit: Max results.

        Returns:
            List of session dicts.
        """
        await self._ensure_initialized()
        if status:
            query = """SELECT * FROM research_sessions WHERE status = ?
                       ORDER BY updated_at DESC LIMIT ?"""
            params: tuple[Any, ...] = (status, limit)
        else:
            query = """SELECT * FROM research_sessions
                       ORDER BY updated_at DESC LIMIT ?"""
            params = (limit,)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def close_session(self, session_id: str) -> None:
        """Close a research session.

        Args:
            session_id: UUID of the session to close.
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE research_sessions SET status = 'closed', updated_at = ?
                   WHERE id = ?""",
                (now, session_id),
            )
            await db.commit()

    # ── Session Papers ────────────────────────────────────────────────

    async def add_session_paper(
        self,
        session_id: str,
        paper_id: str,
        action: str = "read",
        notes: str = "",
    ) -> None:
        """Track a paper within a session.

        Args:
            session_id: UUID of the session.
            paper_id: arXiv paper ID.
            action: Action taken (e.g., 'read', 'cited', 'compared').
            notes: Optional notes about the paper.
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO session_papers (session_id, paper_id, action, notes, added_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, paper_id) DO UPDATE SET
                       action=excluded.action,
                       notes=excluded.notes,
                       added_at=excluded.added_at""",
                (session_id, paper_id, action, notes, now),
            )
            await db.commit()

    async def get_session_papers(self, session_id: str) -> list[dict[str, Any]]:
        """Get all papers tracked in a session.

        Args:
            session_id: UUID of the session.

        Returns:
            List of session paper dicts.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM session_papers WHERE session_id = ?
                   ORDER BY added_at DESC""",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ── Thesis Tracker ────────────────────────────────────────────────

    async def add_thesis(
        self,
        statement: str,
        category: str = "exploratory",
        confidence: float = 0.5,
    ) -> str:
        """Add a new thesis to track.

        Args:
            statement: The thesis statement.
            category: Category (e.g., 'exploratory', 'confirmatory').
            confidence: Initial confidence score (0.0 to 1.0).

        Returns:
            UUID of the created thesis.
        """
        await self._ensure_initialized()
        thesis_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO thesis_tracker
                   (id, statement, category, status, confidence, first_proposed, last_updated, evidence, notes)
                   VALUES (?, ?, ?, 'active', ?, ?, ?, '[]', NULL)""",
                (thesis_id, statement, category, confidence, now, now),
            )
            await db.commit()
        return thesis_id

    async def get_thesis(self, thesis_id: str) -> Optional[dict[str, Any]]:
        """Get a thesis by ID.

        Args:
            thesis_id: UUID of the thesis.

        Returns:
            Thesis dict with parsed evidence JSON, or None.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM thesis_tracker WHERE id = ?", (thesis_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _parse_thesis_row(dict(row))

    async def update_thesis(
        self,
        thesis_id: str,
        confidence: Optional[float] = None,
        status: Optional[str] = None,
        evidence: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Update a thesis.

        Args:
            thesis_id: UUID of the thesis.
            confidence: New confidence score.
            status: New status (e.g., 'active', 'validated', 'invalidated').
            evidence: New evidence string to append to the JSON array.
            notes: New notes (replaces existing).
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # If evidence is provided, fetch current and append
            if evidence is not None:
                cursor = await db.execute(
                    "SELECT evidence FROM thesis_tracker WHERE id = ?",
                    (thesis_id,),
                )
                row = await cursor.fetchone()
                if row is not None:
                    current_evidence = json.loads(row["evidence"])
                    current_evidence.append(evidence)
                    await db.execute(
                        "UPDATE thesis_tracker SET evidence = ?, last_updated = ? WHERE id = ?",
                        (json.dumps(current_evidence), now, thesis_id),
                    )

            if confidence is not None:
                await db.execute(
                    "UPDATE thesis_tracker SET confidence = ?, last_updated = ? WHERE id = ?",
                    (confidence, now, thesis_id),
                )

            if status is not None:
                await db.execute(
                    "UPDATE thesis_tracker SET status = ?, last_updated = ? WHERE id = ?",
                    (status, now, thesis_id),
                )

            if notes is not None:
                await db.execute(
                    "UPDATE thesis_tracker SET notes = ?, last_updated = ? WHERE id = ?",
                    (notes, now, thesis_id),
                )

            await db.commit()

    async def get_active_theses(self) -> list[dict[str, Any]]:
        """Get all theses with status 'active'.

        Returns:
            List of active thesis dicts with parsed evidence.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM thesis_tracker WHERE status = 'active'
                   ORDER BY last_updated DESC"""
            )
            rows = await cursor.fetchall()
            return [_parse_thesis_row(dict(row)) for row in rows]

    # ── Session Digests ───────────────────────────────────────────────

    async def save_digest(
        self,
        content: str,
        session_id: Optional[str] = None,
        validated_theses: Optional[list[str]] = None,
        invalidated_theses: Optional[list[str]] = None,
        emerging_patterns: Optional[list[str]] = None,
        active_opportunities: Optional[list[str]] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> str:
        """Save a session digest.

        Args:
            content: Digest content text.
            session_id: Optional session this digest belongs to.
            validated_theses: List of validated thesis IDs/descriptions.
            invalidated_theses: List of invalidated thesis IDs/descriptions.
            emerging_patterns: List of emerging pattern descriptions.
            active_opportunities: List of active research opportunities.
            meta: Additional metadata dict.

        Returns:
            UUID of the saved digest.
        """
        await self._ensure_initialized()
        digest_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO session_digests
                   (id, session_id, created_at, content, validated_theses,
                    invalidated_theses, emerging_patterns, active_opportunities, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    digest_id,
                    session_id,
                    now,
                    content,
                    json.dumps(validated_theses or []),
                    json.dumps(invalidated_theses or []),
                    json.dumps(emerging_patterns or []),
                    json.dumps(active_opportunities or []),
                    json.dumps(meta or {}),
                ),
            )
            await db.commit()
        return digest_id

    async def get_latest_digest(self) -> Optional[dict[str, Any]]:
        """Get the most recent session digest.

        Returns:
            Digest dict with parsed JSON fields, or None.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_digests ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _parse_digest_row(dict(row))

    # ── Warm Context ──────────────────────────────────────────────────

    async def get_warm_context(self) -> dict[str, Any]:
        """Get warm context for session continuity.

        Returns:
            Dict with total_prior_runs, latest_digest, and active_theses.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM research_sessions"
            )
            row = await cursor.fetchone()
            total_prior_runs = row[0] if row else 0

        latest_digest = await self.get_latest_digest()
        active_theses = await self.get_active_theses()

        return {
            "total_prior_runs": total_prior_runs,
            "latest_digest": latest_digest,
            "active_theses": active_theses,
        }


def _parse_thesis_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON fields in a thesis row."""
    result = dict(row)
    evidence_raw = result.get("evidence", "[]")
    if isinstance(evidence_raw, str):
        try:
            result["evidence"] = json.loads(evidence_raw)
        except json.JSONDecodeError:
            result["evidence"] = []
    return result


def _parse_digest_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON fields in a digest row."""
    result = dict(row)
    for field in (
        "validated_theses",
        "invalidated_theses",
        "emerging_patterns",
        "active_opportunities",
    ):
        val = result.get(field, "[]")
        if isinstance(val, str):
            try:
                result[field] = json.loads(val)
            except json.JSONDecodeError:
                result[field] = []
    meta_raw = result.get("meta", "{}")
    if isinstance(meta_raw, str):
        try:
            result["meta"] = json.loads(meta_raw)
        except json.JSONDecodeError:
            result["meta"] = {}
    return result
