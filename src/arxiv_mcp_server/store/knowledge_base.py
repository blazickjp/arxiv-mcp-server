"""Personal research knowledge base with structured organization and vector search.

Source-agnostic paper storage with tags, collections, annotations,
reading status, and embedding-based semantic search. arXiv is one
source — papers can also be added manually or from DOIs.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from ..config import Settings

logger = logging.getLogger("arxiv-mcp-server")

_CREATE_KB_TABLES = """
CREATE TABLE IF NOT EXISTS kb_papers (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    abstract TEXT,
    full_text_path TEXT,
    categories TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    reading_status TEXT NOT NULL DEFAULT 'unread',
    citation_count INTEGER,
    published_date TEXT,
    url TEXT,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_collections (
    name TEXT PRIMARY KEY,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_collection_papers (
    collection_name TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (collection_name, paper_id),
    FOREIGN KEY (collection_name) REFERENCES kb_collections(name) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES kb_papers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kb_embeddings (
    paper_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, model_name),
    FOREIGN KEY (paper_id) REFERENCES kb_papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kb_papers_source ON kb_papers(source);
CREATE INDEX IF NOT EXISTS idx_kb_papers_reading_status ON kb_papers(reading_status);
CREATE INDEX IF NOT EXISTS idx_kb_papers_added_at ON kb_papers(added_at);
"""


class KnowledgeBase:
    """Personal research knowledge base with vector search.

    Stores papers from any source with structured metadata, tags,
    collections, annotations, and embeddings for semantic search.

    Args:
        db_path: Path to the SQLite database. Defaults to
            {storage_path}/knowledge_base.db.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            settings = Settings()
            db_path = settings.STORAGE_PATH / "knowledge_base.db"
        self.db_path = db_path
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_CREATE_KB_TABLES)
            await db.commit()
        self._initialized = True

    # ── Paper CRUD ──────────────────────────────────────────────

    async def save_paper(self, paper: dict[str, Any]) -> str:
        """Save or update a paper in the knowledge base.

        Args:
            paper: Paper dict. Required: id, title.
                Optional: source, source_id, authors, abstract, categories,
                tags, notes, reading_status, citation_count, published_date,
                url, full_text_path.

        Returns:
            The paper ID.
        """
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        paper_id = paper["id"]

        async with aiosqlite.connect(self.db_path) as db:
            # Check if exists
            cursor = await db.execute(
                "SELECT id FROM kb_papers WHERE id = ?", (paper_id,)
            )
            exists = await cursor.fetchone()

            if exists:
                await db.execute(
                    """UPDATE kb_papers SET
                        title = COALESCE(?, title),
                        authors = COALESCE(?, authors),
                        abstract = COALESCE(?, abstract),
                        categories = COALESCE(?, categories),
                        tags = COALESCE(?, tags),
                        notes = COALESCE(?, notes),
                        reading_status = COALESCE(?, reading_status),
                        citation_count = COALESCE(?, citation_count),
                        published_date = COALESCE(?, published_date),
                        url = COALESCE(?, url),
                        full_text_path = COALESCE(?, full_text_path),
                        source = COALESCE(?, source),
                        source_id = COALESCE(?, source_id),
                        updated_at = ?
                    WHERE id = ?""",
                    (
                        paper.get("title"),
                        json.dumps(paper["authors"]) if "authors" in paper else None,
                        paper.get("abstract"),
                        json.dumps(paper["categories"]) if "categories" in paper else None,
                        json.dumps(paper["tags"]) if "tags" in paper else None,
                        paper.get("notes"),
                        paper.get("reading_status"),
                        paper.get("citation_count"),
                        paper.get("published_date"),
                        paper.get("url"),
                        paper.get("full_text_path"),
                        paper.get("source"),
                        paper.get("source_id"),
                        now,
                        paper_id,
                    ),
                )
            else:
                await db.execute(
                    """INSERT INTO kb_papers (
                        id, source, source_id, title, authors, abstract,
                        full_text_path, categories, tags, notes,
                        reading_status, citation_count, published_date,
                        url, added_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        paper_id,
                        paper.get("source", "manual"),
                        paper.get("source_id"),
                        paper["title"],
                        json.dumps(paper.get("authors", [])),
                        paper.get("abstract"),
                        paper.get("full_text_path"),
                        json.dumps(paper.get("categories", [])),
                        json.dumps(paper.get("tags", [])),
                        paper.get("notes"),
                        paper.get("reading_status", "unread"),
                        paper.get("citation_count"),
                        paper.get("published_date"),
                        paper.get("url"),
                        now,
                        now,
                    ),
                )
            await db.commit()
        return paper_id

    async def get_paper(self, paper_id: str) -> Optional[dict[str, Any]]:
        """Get a paper by ID."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM kb_papers WHERE id = ?", (paper_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            paper = _row_to_dict(dict(row))

            # Fetch collections this paper belongs to
            cursor = await db.execute(
                "SELECT collection_name FROM kb_collection_papers WHERE paper_id = ?",
                (paper_id,),
            )
            rows = await cursor.fetchall()
            paper["collections"] = [r[0] for r in rows]
            return paper

    async def remove_paper(self, paper_id: str) -> bool:
        """Remove a paper and its embeddings from the KB.

        Returns:
            True if paper was found and removed.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM kb_papers WHERE id = ?", (paper_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_papers(
        self,
        *,
        tags: Optional[list[str]] = None,
        categories: Optional[list[str]] = None,
        reading_status: Optional[str] = None,
        collection: Optional[str] = None,
        source: Optional[str] = None,
        query: Optional[str] = None,
        sort_by: str = "added_at",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List papers with optional filters.

        Args:
            tags: Filter by any of these tags.
            categories: Filter by any of these categories.
            reading_status: Filter by reading status.
            collection: Filter by collection name.
            source: Filter by source (arxiv, doi, manual).
            query: Text search in title and abstract.
            sort_by: Sort field (added_at, updated_at, title, published_date).
            sort_order: asc or desc.
            limit: Max results.
            offset: Skip first N results.

        Returns:
            List of paper dicts.
        """
        await self._ensure_initialized()
        conditions: list[str] = []
        params: list[Any] = []

        if reading_status:
            conditions.append("p.reading_status = ?")
            params.append(reading_status)

        if source:
            conditions.append("p.source = ?")
            params.append(source)

        if query:
            # Split query into words and match each independently
            words = query.strip().split()
            word_conditions = []
            for word in words:
                pattern = f"%{word}%"
                word_conditions.append("(p.title LIKE ? OR p.abstract LIKE ?)")
                params.extend([pattern, pattern])
            if word_conditions:
                conditions.append(f"({' AND '.join(word_conditions)})")

        if tags:
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("p.tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        if categories:
            cat_conditions = []
            for cat in categories:
                cat_conditions.append("p.categories LIKE ?")
                params.append(f'%"{cat}"%')
            conditions.append(f"({' OR '.join(cat_conditions)})")

        # Build query
        if collection:
            base = (
                "SELECT p.* FROM kb_papers p "
                "JOIN kb_collection_papers cp ON p.id = cp.paper_id "
                "WHERE cp.collection_name = ?"
            )
            params.insert(0, collection)
        else:
            base = "SELECT p.* FROM kb_papers p WHERE 1=1"

        if conditions:
            if collection:
                base += " AND " + " AND ".join(conditions)
            else:
                base = base.replace("WHERE 1=1", "WHERE " + " AND ".join(conditions))

        # Sort
        valid_sorts = {"added_at", "updated_at", "title", "published_date"}
        sort_col = sort_by if sort_by in valid_sorts else "added_at"
        order = "ASC" if sort_order.lower() == "asc" else "DESC"
        base += f" ORDER BY p.{sort_col} {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(base, params)
            rows = await cursor.fetchall()
            return [_row_to_dict(dict(r)) for r in rows]

    async def count_papers(
        self, *, reading_status: Optional[str] = None, source: Optional[str] = None
    ) -> int:
        """Count papers in KB with optional filters."""
        await self._ensure_initialized()
        conditions: list[str] = []
        params: list[Any] = []
        if reading_status:
            conditions.append("reading_status = ?")
            params.append(reading_status)
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = " AND ".join(conditions) if conditions else "1=1"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM kb_papers WHERE {where}", params
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    # ── Annotations ─────────────────────────────────────────────

    async def annotate(
        self,
        paper_id: str,
        *,
        tags: Optional[list[str]] = None,
        notes: Optional[str] = None,
        reading_status: Optional[str] = None,
        add_tags: Optional[list[str]] = None,
        remove_tags: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Update annotations on a paper.

        Args:
            paper_id: Paper to annotate.
            tags: Replace all tags (overwrite).
            notes: Set/replace notes.
            reading_status: Set reading status (unread/reading/completed/archived).
            add_tags: Add tags to existing set.
            remove_tags: Remove specific tags.

        Returns:
            Updated paper dict, or None if not found.
        """
        await self._ensure_initialized()
        paper = await self.get_paper(paper_id)
        if paper is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        updates: list[str] = []
        params: list[Any] = []

        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))
        elif add_tags or remove_tags:
            current_tags = set(paper.get("tags", []))
            if add_tags:
                current_tags.update(add_tags)
            if remove_tags:
                current_tags -= set(remove_tags)
            updates.append("tags = ?")
            params.append(json.dumps(sorted(current_tags)))

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if reading_status is not None:
            valid = {"unread", "reading", "completed", "archived"}
            if reading_status not in valid:
                raise ValueError(
                    f"Invalid reading_status '{reading_status}'. "
                    f"Must be one of: {', '.join(sorted(valid))}"
                )
            updates.append("reading_status = ?")
            params.append(reading_status)

        if not updates:
            return paper

        updates.append("updated_at = ?")
        params.append(now)
        params.append(paper_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE kb_papers SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()

        return await self.get_paper(paper_id)

    # ── Collections ─────────────────────────────────────────────

    async def create_collection(
        self, name: str, description: Optional[str] = None
    ) -> dict[str, Any]:
        """Create a new collection."""
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO kb_collections (name, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (name, description, now, now),
            )
            await db.commit()
        return {"name": name, "description": description, "created_at": now}

    async def add_to_collection(self, collection_name: str, paper_id: str) -> bool:
        """Add a paper to a collection."""
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO kb_collection_papers "
                    "(collection_name, paper_id, added_at) VALUES (?, ?, ?)",
                    (collection_name, paper_id, now),
                )
                await db.commit()
            return True
        except Exception:
            return False

    async def remove_from_collection(self, collection_name: str, paper_id: str) -> bool:
        """Remove a paper from a collection."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM kb_collection_papers WHERE collection_name = ? AND paper_id = ?",
                (collection_name, paper_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_collections(self) -> list[dict[str, Any]]:
        """List all collections with paper counts."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT c.*, COUNT(cp.paper_id) as paper_count
                FROM kb_collections c
                LEFT JOIN kb_collection_papers cp ON c.name = cp.collection_name
                GROUP BY c.name
                ORDER BY c.name"""
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Embeddings ──────────────────────────────────────────────

    async def save_embedding(
        self, paper_id: str, model_name: str, embedding: bytes
    ) -> None:
        """Store an embedding for a KB paper."""
        await self._ensure_initialized()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO kb_embeddings (paper_id, model_name, embedding, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(paper_id, model_name) DO UPDATE SET
                    embedding = excluded.embedding, created_at = excluded.created_at""",
                (paper_id, model_name, embedding, now),
            )
            await db.commit()

    async def get_embedding(
        self, paper_id: str, model_name: str
    ) -> Optional[bytes]:
        """Get cached embedding for a KB paper."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT embedding FROM kb_embeddings WHERE paper_id = ? AND model_name = ?",
                (paper_id, model_name),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_all_papers_with_embeddings(
        self, model_name: str
    ) -> list[tuple[dict[str, Any], bytes]]:
        """Get all KB papers that have embeddings.

        Returns:
            List of (paper_dict, embedding_bytes) tuples.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT p.*, e.embedding
                FROM kb_papers p
                JOIN kb_embeddings e ON p.id = e.paper_id
                WHERE e.model_name = ?""",
                (model_name,),
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                row_dict = dict(row)
                emb = row_dict.pop("embedding")
                results.append((_row_to_dict(row_dict), emb))
            return results

    async def get_stats(self) -> dict[str, Any]:
        """Get KB statistics."""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            # Total papers
            cursor = await db.execute("SELECT COUNT(*) FROM kb_papers")
            total = (await cursor.fetchone())[0]

            # By source
            cursor = await db.execute(
                "SELECT source, COUNT(*) FROM kb_papers GROUP BY source"
            )
            by_source = {r[0]: r[1] for r in await cursor.fetchall()}

            # By reading status
            cursor = await db.execute(
                "SELECT reading_status, COUNT(*) FROM kb_papers GROUP BY reading_status"
            )
            by_status = {r[0]: r[1] for r in await cursor.fetchall()}

            # Collections
            cursor = await db.execute("SELECT COUNT(*) FROM kb_collections")
            n_collections = (await cursor.fetchone())[0]

            # Embeddings
            cursor = await db.execute(
                "SELECT COUNT(DISTINCT paper_id) FROM kb_embeddings"
            )
            n_embedded = (await cursor.fetchone())[0]

            # Top tags
            cursor = await db.execute("SELECT tags FROM kb_papers WHERE tags != '[]'")
            tag_counts: dict[str, int] = {}
            for row in await cursor.fetchall():
                for tag in json.loads(row[0]):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        return {
            "total_papers": total,
            "by_source": by_source,
            "by_reading_status": by_status,
            "collections": n_collections,
            "papers_with_embeddings": n_embedded,
            "top_tags": top_tags,
        }


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a SQLite row to a paper dict with parsed JSON fields."""
    result = dict(row)
    for field in ("authors", "categories", "tags"):
        val = result.get(field)
        if isinstance(val, str):
            try:
                result[field] = json.loads(val)
            except json.JSONDecodeError:
                result[field] = []
    return result
