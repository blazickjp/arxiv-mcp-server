"""SQLite-backed research session state tracking across MCP calls.

Maintains session-level research context: which papers have been examined,
open questions/threads to follow, and key findings with evidence links.
Persists to {storage_path}/research_context.db.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    goal TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_papers (
    session_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    action TEXT NOT NULL,
    notes TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (session_id, paper_id, action),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    answer TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    finding TEXT NOT NULL,
    evidence_paper_ids TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_session_papers_session ON session_papers(session_id);
CREATE INDEX IF NOT EXISTS idx_session_threads_session ON session_threads(session_id);
CREATE INDEX IF NOT EXISTS idx_session_findings_session ON session_findings(session_id);
"""


class ResearchContext:
    """Session-level research state tracker.

    Tracks research sessions with their associated papers, open questions,
    and key findings. State persists across MCP calls via SQLite.

    Args:
        db_path: Path to the SQLite database. Defaults to
            {storage_path}/research_context.db.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            settings = Settings()
            db_path = settings.STORAGE_PATH / "research_context.db"
        self.db_path = db_path
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_TABLES)
            await db.commit()
        self._initialized = True

    # -- Sessions --------------------------------------------------------

    async def create_session(
        self, name: str, goal: Optional[str] = None
    ) -> dict[str, Any]:
        """Create a new research session.

        Args:
            name: Human-readable session name.
            goal: Optional research goal or question.

        Returns:
            Dict with session id, name, goal, status, and timestamps.
        """
        await self._ensure_initialized()
        session_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO sessions (id, name, goal, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)""",
                (session_id, name, goal, now, now),
            )
            await db.commit()

        return {
            "id": session_id,
            "name": name,
            "goal": goal,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    async def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """Get a session with aggregate counts for papers, threads, findings.

        Args:
            session_id: The session identifier.

        Returns:
            Session dict with counts, or None if not found.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            session = dict(row)

            # Paper count
            cursor = await db.execute(
                "SELECT COUNT(DISTINCT paper_id) FROM session_papers WHERE session_id = ?",
                (session_id,),
            )
            session["paper_count"] = (await cursor.fetchone())[0]

            # Thread counts
            cursor = await db.execute(
                "SELECT status, COUNT(*) FROM session_threads "
                "WHERE session_id = ? GROUP BY status",
                (session_id,),
            )
            thread_counts = {r[0]: r[1] for r in await cursor.fetchall()}
            session["threads_open"] = thread_counts.get("open", 0)
            session["threads_answered"] = thread_counts.get("answered", 0)
            session["threads_parked"] = thread_counts.get("parked", 0)

            # Findings count
            cursor = await db.execute(
                "SELECT COUNT(*) FROM session_findings WHERE session_id = ?",
                (session_id,),
            )
            session["findings_count"] = (await cursor.fetchone())[0]

            return session

    async def list_sessions(
        self, status: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """List all sessions, optionally filtered by status.

        Args:
            status: Filter by session status (active, paused, completed).

        Returns:
            List of session dicts with aggregate counts.
        """
        await self._ensure_initialized()
        if status:
            query = "SELECT id FROM sessions WHERE status = ? ORDER BY updated_at DESC"
            params: tuple[Any, ...] = (status,)
        else:
            query = "SELECT id FROM sessions ORDER BY updated_at DESC"
            params = ()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

        sessions = []
        for row in rows:
            session = await self.get_session(row[0])
            if session is not None:
                sessions.append(session)
        return sessions

    async def update_session(
        self,
        session_id: str,
        *,
        name: Optional[str] = None,
        goal: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Update session fields.

        Args:
            session_id: The session identifier.
            name: New session name.
            goal: New research goal.
            status: New status (active, paused, completed).

        Returns:
            Updated session dict, or None if not found.
        """
        await self._ensure_initialized()
        updates: list[str] = []
        params: list[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if goal is not None:
            updates.append("goal = ?")
            params.append(goal)
        if status is not None:
            valid = {"active", "paused", "completed"}
            if status not in valid:
                raise ValueError(
                    f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid))}"
                )
            updates.append("status = ?")
            params.append(status)

        if not updates:
            return await self.get_session(session_id)

        now = datetime.now(timezone.utc).isoformat()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(session_id)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()
            if cursor.rowcount == 0:
                return None

        return await self.get_session(session_id)

    # -- Papers ----------------------------------------------------------

    async def log_paper(
        self,
        session_id: str,
        paper_id: str,
        action: str,
        notes: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record a paper interaction within a session.

        Args:
            session_id: The session identifier.
            paper_id: The paper identifier (e.g. arXiv ID).
            action: What was done (searched, read, saved, compared, cited).
            notes: Optional notes about this interaction.

        Returns:
            Dict confirming the logged interaction.
        """
        await self._ensure_initialized()
        valid_actions = {"searched", "read", "saved", "compared", "cited"}
        if action not in valid_actions:
            raise ValueError(
                f"Invalid action '{action}'. "
                f"Must be one of: {', '.join(sorted(valid_actions))}"
            )

        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO session_papers
                (session_id, paper_id, action, notes, added_at)
                VALUES (?, ?, ?, ?, ?)""",
                (session_id, paper_id, action, notes, now),
            )
            # Touch session updated_at
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            await db.commit()

        return {
            "session_id": session_id,
            "paper_id": paper_id,
            "action": action,
            "notes": notes,
            "added_at": now,
        }

    # -- Threads ---------------------------------------------------------

    async def add_thread(
        self, session_id: str, question: str
    ) -> dict[str, Any]:
        """Add an open research question to a session.

        Args:
            session_id: The session identifier.
            question: The research question or thread to follow.

        Returns:
            Dict with thread id, question, status, and timestamps.
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO session_threads
                (session_id, question, status, created_at, updated_at)
                VALUES (?, ?, 'open', ?, ?)""",
                (session_id, question, now, now),
            )
            thread_id = cursor.lastrowid
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            await db.commit()

        return {
            "id": thread_id,
            "session_id": session_id,
            "question": question,
            "status": "open",
            "answer": None,
            "created_at": now,
            "updated_at": now,
        }

    async def update_thread(
        self,
        thread_id: int,
        *,
        status: Optional[str] = None,
        answer: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Update a research thread (mark answered, park, etc.).

        Args:
            thread_id: The thread identifier.
            status: New status (open, answered, parked).
            answer: The answer text (typically set when status='answered').

        Returns:
            Updated thread dict, or None if not found.
        """
        await self._ensure_initialized()
        updates: list[str] = []
        params: list[Any] = []

        if status is not None:
            valid = {"open", "answered", "parked"}
            if status not in valid:
                raise ValueError(
                    f"Invalid thread status '{status}'. "
                    f"Must be one of: {', '.join(sorted(valid))}"
                )
            updates.append("status = ?")
            params.append(status)

        if answer is not None:
            updates.append("answer = ?")
            params.append(answer)

        if not updates:
            return None

        now = datetime.now(timezone.utc).isoformat()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(thread_id)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE session_threads SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            if cursor.rowcount == 0:
                await db.commit()
                return None

            # Touch parent session
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_threads WHERE id = ?", (thread_id,)
            )
            thread_row = await cursor.fetchone()
            if thread_row:
                await db.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, thread_row["session_id"]),
                )
            await db.commit()

            if thread_row is None:
                return None
            return dict(thread_row)

    # -- Findings --------------------------------------------------------

    async def add_finding(
        self,
        session_id: str,
        finding: str,
        evidence_paper_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Record a key research finding with optional evidence links.

        Args:
            session_id: The session identifier.
            finding: The finding text.
            evidence_paper_ids: List of paper IDs that support this finding.

        Returns:
            Dict with finding id, text, evidence, and timestamp.
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        evidence = json.dumps(evidence_paper_ids or [])

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO session_findings
                (session_id, finding, evidence_paper_ids, created_at)
                VALUES (?, ?, ?, ?)""",
                (session_id, finding, evidence, now),
            )
            finding_id = cursor.lastrowid
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            await db.commit()

        return {
            "id": finding_id,
            "session_id": session_id,
            "finding": finding,
            "evidence_paper_ids": evidence_paper_ids or [],
            "created_at": now,
        }

    # -- Summaries -------------------------------------------------------

    async def get_session_summary(
        self, session_id: str
    ) -> Optional[dict[str, Any]]:
        """Get a full session summary with all papers, threads, and findings.

        Args:
            session_id: The session identifier.

        Returns:
            Complete session dict, or None if not found.
        """
        await self._ensure_initialized()
        session = await self.get_session(session_id)
        if session is None:
            return None

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # All papers
            cursor = await db.execute(
                "SELECT * FROM session_papers WHERE session_id = ? ORDER BY added_at DESC",
                (session_id,),
            )
            session["papers"] = [dict(r) for r in await cursor.fetchall()]

            # All threads
            cursor = await db.execute(
                "SELECT * FROM session_threads WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            session["threads"] = [dict(r) for r in await cursor.fetchall()]

            # All findings
            cursor = await db.execute(
                "SELECT * FROM session_findings WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            findings = []
            for row in await cursor.fetchall():
                f = dict(row)
                try:
                    f["evidence_paper_ids"] = json.loads(
                        f.get("evidence_paper_ids", "[]")
                    )
                except json.JSONDecodeError:
                    f["evidence_paper_ids"] = []
                findings.append(f)
            session["findings"] = findings

        return session

    async def get_active_session(self) -> Optional[dict[str, Any]]:
        """Get the most recently updated active session.

        Returns:
            Session dict with counts, or None if no active session exists.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM sessions WHERE status = 'active' "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return await self.get_session(row[0])
