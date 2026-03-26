"""SQLite-based persistence for paper metadata and embedding cache."""

import json
import logging
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    abstract TEXT,
    categories TEXT NOT NULL,
    published TEXT,
    updated TEXT,
    pdf_url TEXT,
    doi TEXT,
    s2_paper_id TEXT,
    citation_count INTEGER,
    influential_citation_count INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embeddings (
    paper_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paper_id, model_name)
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paper_count INTEGER,
    digest_json TEXT NOT NULL
);
"""


class SQLiteStore:
    """Async SQLite store for paper metadata and embeddings.

    Uses a single database at {storage_path}/arxiv_cache.db.
    Tables are created on first use.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            settings = Settings()
            db_path = settings.STORAGE_PATH / "arxiv_cache.db"
        self.db_path = db_path
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create tables if they don't exist yet."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_TABLES_SQL)
            await db.commit()
        self._initialized = True

    async def get_paper(self, paper_id: str) -> Optional[dict[str, Any]]:
        """Get cached paper metadata by ID.

        Args:
            paper_id: arXiv paper ID (without version suffix).

        Returns:
            Paper dict or None if not cached.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM papers WHERE paper_id = ?", (paper_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_paper_dict(dict(row))

    async def upsert_paper(self, paper: dict[str, Any]) -> None:
        """Insert or update paper metadata.

        Args:
            paper: Paper dict with at minimum paper_id, title, authors, categories.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO papers (
                    paper_id, title, authors, abstract, categories,
                    published, updated, pdf_url, doi, s2_paper_id,
                    citation_count, influential_citation_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    title=excluded.title,
                    authors=excluded.authors,
                    abstract=excluded.abstract,
                    categories=excluded.categories,
                    published=excluded.published,
                    updated=excluded.updated,
                    pdf_url=excluded.pdf_url,
                    doi=excluded.doi,
                    s2_paper_id=excluded.s2_paper_id,
                    citation_count=excluded.citation_count,
                    influential_citation_count=excluded.influential_citation_count,
                    fetched_at=CURRENT_TIMESTAMP
                """,
                (
                    paper.get("paper_id", paper.get("id", "")),
                    paper.get("title", ""),
                    json.dumps(paper.get("authors", [])),
                    paper.get("abstract"),
                    json.dumps(paper.get("categories", [])),
                    paper.get("published"),
                    paper.get("updated"),
                    paper.get("pdf_url", paper.get("url")),
                    paper.get("doi"),
                    paper.get("s2_paper_id"),
                    paper.get("citation_count"),
                    paper.get("influential_citation_count"),
                ),
            )
            await db.commit()

    async def upsert_papers(self, papers: list[dict[str, Any]]) -> None:
        """Batch insert/update multiple papers.

        Args:
            papers: List of paper dicts.
        """
        for paper in papers:
            await self.upsert_paper(paper)

    async def get_embedding(
        self, paper_id: str, model_name: str
    ) -> Optional[bytes]:
        """Get cached embedding for a paper.

        Args:
            paper_id: arXiv paper ID.
            model_name: Name of the embedding model.

        Returns:
            Raw embedding bytes (numpy array) or None.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT embedding FROM embeddings WHERE paper_id = ? AND model_name = ?",
                (paper_id, model_name),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def upsert_embedding(
        self, paper_id: str, model_name: str, embedding: bytes
    ) -> None:
        """Cache an embedding for a paper.

        Args:
            paper_id: arXiv paper ID.
            model_name: Name of the embedding model.
            embedding: Raw embedding bytes (numpy array as bytes).
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO embeddings (paper_id, model_name, embedding)
                VALUES (?, ?, ?)
                ON CONFLICT(paper_id, model_name) DO UPDATE SET
                    embedding=excluded.embedding,
                    created_at=CURRENT_TIMESTAMP
                """,
                (paper_id, model_name, embedding),
            )
            await db.commit()

    async def save_digest(
        self, topic: str, paper_count: int, digest_json: str
    ) -> int:
        """Save a generated digest.

        Args:
            topic: Research topic.
            paper_count: Number of papers in the digest.
            digest_json: Full digest as JSON string.

        Returns:
            ID of the saved digest row.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO digests (topic, paper_count, digest_json) VALUES (?, ?, ?)",
                (topic, paper_count, digest_json),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def search_papers_cached(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search cached papers by title/abstract text match.

        Args:
            query: Search text (uses SQL LIKE).
            limit: Max results.

        Returns:
            List of matching paper dicts.
        """
        await self._ensure_initialized()
        like_pattern = f"%{query}%"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM papers
                WHERE title LIKE ? OR abstract LIKE ?
                ORDER BY fetched_at DESC
                LIMIT ?""",
                (like_pattern, like_pattern, limit),
            )
            rows = await cursor.fetchall()
            return [_row_to_paper_dict(dict(row)) for row in rows]


def _row_to_paper_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a SQLite row to a paper dict with parsed JSON fields."""
    result = dict(row)
    # Parse JSON array fields
    for field in ("authors", "categories"):
        val = result.get(field)
        if isinstance(val, str):
            try:
                result[field] = json.loads(val)
            except json.JSONDecodeError:
                result[field] = []
    return result
