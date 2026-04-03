"""Semantic search and indexing tools for the arXiv MCP server."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import arxiv
import mcp.types as types

from ..config import Settings

try:
    import numpy as np
except ImportError:  # pragma: no cover - handled gracefully in runtime checks
    np = None  # type: ignore[assignment]

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - handled gracefully in runtime checks
    SentenceTransformer = None  # type: ignore[assignment]

logger = logging.getLogger("arxiv-mcp-server")
settings = Settings()

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_DB_NAME = "semantic_index.db"

_model: Optional[Any] = None


@dataclass
class IndexedPaper:
    """Stored paper payload used for similarity ranking."""

    paper_id: str
    title: str
    abstract: str
    authors: List[str]
    categories: List[str]
    published: str
    score: float


semantic_search_tool = types.Tool(
    name="semantic_search",
    description=(
        "Semantic similarity search over papers you have already downloaded locally via download_paper. "
        "Supports free-text queries (e.g. 'attention mechanisms for long sequences') or finding papers "
        "similar to a given paper_id. "
        "IMPORTANT: only searches your local downloaded collection — will return empty results if no papers "
        "have been downloaded yet. Use search_papers to find papers on arXiv, then download_paper to add "
        "them to the local index before using this tool. "
        "Requires pro dependencies: uv pip install -e \".[pro]\""
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text semantic query.",
            },
            "paper_id": {
                "type": "string",
                "description": "Find papers semantically similar to this arXiv paper ID.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 10).",
                "default": 10,
            },
        },
    },
)


reindex_tool = types.Tool(
    name="reindex",
    description="Rebuild the local semantic index for downloaded papers.",
    inputSchema={
        "type": "object",
        "properties": {
            "clear_existing": {
                "type": "boolean",
                "description": "If true, clear the existing index before rebuilding.",
                "default": True,
            }
        },
    },
)


def _dependency_error() -> Optional[str]:
    """Return a friendly dependency error if pro packages are missing."""
    if np is None or SentenceTransformer is None:
        return (
            "Pro feature dependency missing. Install with: "
            "`uv pip install -e \".[pro]\"`"
        )
    return None


def _db_path() -> Path:
    """Return the semantic index SQLite path."""
    return Path(settings.STORAGE_PATH) / INDEX_DB_NAME


def _connect() -> sqlite3.Connection:
    """Open SQLite connection and ensure schema exists."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_index (
            paper_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            abstract TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            published TEXT,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _get_model() -> Any:
    """Load the sentence-transformers model lazily."""
    global _model
    if _model is None:
        logger.info("Loading semantic embedding model %s", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME, silent=True)
    return _model


def _embed_text(text: str) -> Any:
    """Create embedding vector for a text payload."""
    model = _get_model()
    return model.encode(text or "", convert_to_numpy=True, normalize_embeddings=True)


def _upsert_index_record(
    paper_id: str,
    title: str,
    abstract: str,
    authors: List[str],
    categories: List[str],
    published: str = "",
) -> bool:
    """Insert or update an index record for a paper."""
    dependency_error = _dependency_error()
    if dependency_error:
        logger.warning(dependency_error)
        return False

    embedding = _embed_text(abstract)
    embedding_array = np.asarray(embedding, dtype=np.float32)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO semantic_index (
                paper_id, title, abstract, authors_json, categories_json,
                published, embedding, embedding_dim, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                title=excluded.title,
                abstract=excluded.abstract,
                authors_json=excluded.authors_json,
                categories_json=excluded.categories_json,
                published=excluded.published,
                embedding=excluded.embedding,
                embedding_dim=excluded.embedding_dim,
                updated_at=excluded.updated_at
            """,
            (
                paper_id,
                title,
                abstract,
                json.dumps(authors),
                json.dumps(categories),
                published,
                embedding_array.tobytes(),
                int(embedding_array.shape[0]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    return True


def index_paper_by_id(paper_id: str) -> bool:
    """Fetch arXiv metadata by ID and add/update it in the semantic index."""
    try:
        client = arxiv.Client()
        paper = next(client.results(arxiv.Search(id_list=[paper_id])))
    except StopIteration:
        logger.warning("Could not index paper %s: not found on arXiv", paper_id)
        return False
    except Exception as exc:
        logger.error("Could not fetch metadata for %s: %s", paper_id, exc)
        return False

    return index_paper_from_result(paper)


def index_paper_from_result(paper: Any) -> bool:
    """Index a paper from an arxiv.Result-like object."""
    try:
        paper_id = paper.get_short_id()
        title = paper.title or ""
        abstract = paper.summary or ""
        authors = [author.name for author in getattr(paper, "authors", [])]
        categories = list(getattr(paper, "categories", []) or [])
        published = ""
        if getattr(paper, "published", None) is not None:
            published = paper.published.isoformat()

        if not abstract.strip():
            logger.warning("Skipping semantic indexing for %s: empty abstract", paper_id)
            return False

        return _upsert_index_record(
            paper_id=paper_id,
            title=title,
            abstract=abstract,
            authors=authors,
            categories=categories,
            published=published,
        )
    except Exception as exc:
        logger.error("Failed indexing paper from result: %s", exc)
        return False


def _load_vectors(exclude_paper_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load all vectors (optionally excluding one paper)."""
    with _connect() as conn:
        if exclude_paper_id:
            rows = conn.execute(
                "SELECT * FROM semantic_index WHERE paper_id != ?", (exclude_paper_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM semantic_index").fetchall()

    vectors: List[Dict[str, Any]] = []
    for row in rows:
        vector = np.frombuffer(row["embedding"], dtype=np.float32, count=row["embedding_dim"])
        vectors.append(
            {
                "paper_id": row["paper_id"],
                "title": row["title"],
                "abstract": row["abstract"],
                "authors": json.loads(row["authors_json"]),
                "categories": json.loads(row["categories_json"]),
                "published": row["published"] or "",
                "vector": vector,
            }
        )
    return vectors


def _rank_by_similarity(
    query_vector: Any, candidates: List[Dict[str, Any]], max_results: int
) -> List[IndexedPaper]:
    """Compute cosine similarity (normalized vectors) and rank results."""
    if not candidates:
        return []

    matrix = np.vstack([candidate["vector"] for candidate in candidates])
    similarities = matrix @ np.asarray(query_vector, dtype=np.float32)

    ranked_indices = np.argsort(similarities)[::-1][:max_results]
    ranked_results: List[IndexedPaper] = []

    for idx in ranked_indices:
        candidate = candidates[int(idx)]
        ranked_results.append(
            IndexedPaper(
                paper_id=candidate["paper_id"],
                title=candidate["title"],
                abstract=candidate["abstract"],
                authors=candidate["authors"],
                categories=candidate["categories"],
                published=candidate["published"],
                score=float(similarities[int(idx)]),
            )
        )

    return ranked_results


def _get_indexed_paper_vector(paper_id: str) -> Optional[Any]:
    """Fetch an indexed vector for a specific paper."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT embedding, embedding_dim FROM semantic_index WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()

    if row is None:
        return None

    return np.frombuffer(row["embedding"], dtype=np.float32, count=row["embedding_dim"])


def rebuild_index(clear_existing: bool = True) -> Dict[str, Any]:
    """Rebuild semantic index from downloaded markdown papers."""
    dependency_error = _dependency_error()
    if dependency_error:
        return {"status": "error", "message": dependency_error}

    paper_ids = sorted(p.stem for p in Path(settings.STORAGE_PATH).glob("*.md"))

    if clear_existing:
        with _connect() as conn:
            conn.execute("DELETE FROM semantic_index")
            conn.commit()

    indexed = 0
    failed: List[str] = []

    for paper_id in paper_ids:
        success = index_paper_by_id(paper_id)
        if success:
            indexed += 1
        else:
            failed.append(paper_id)

    return {
        "status": "success",
        "indexed": indexed,
        "failed": failed,
        "total_local_papers": len(paper_ids),
    }


async def handle_reindex(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle reindex tool calls."""
    try:
        clear_existing = bool(arguments.get("clear_existing", True))
        result = rebuild_index(clear_existing=clear_existing)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as exc:
        logger.error("Reindex failed: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]


async def handle_semantic_search(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle semantic search queries and similar-paper lookups."""
    try:
        dependency_error = _dependency_error()
        if dependency_error:
            return [types.TextContent(type="text", text=f"Error: {dependency_error}")]

        query = (arguments.get("query") or "").strip()
        paper_id = (arguments.get("paper_id") or "").strip()
        max_results = min(int(arguments.get("max_results", 10)), settings.MAX_RESULTS)

        if not query and not paper_id:
            return [
                types.TextContent(
                    type="text",
                    text="Error: Provide either `query` or `paper_id` for semantic_search.",
                )
            ]

        if paper_id:
            query_vector = _get_indexed_paper_vector(paper_id)
            if query_vector is None:
                logger.info("Paper %s not indexed yet, attempting to fetch and index", paper_id)
                if not index_paper_by_id(paper_id):
                    return [
                        types.TextContent(
                            type="text",
                            text=f"Error: Could not index source paper {paper_id}.",
                        )
                    ]
                query_vector = _get_indexed_paper_vector(paper_id)

            candidates = _load_vectors(exclude_paper_id=paper_id)
            mode = "similar_to_paper"
            query_payload = paper_id
        else:
            query_vector = _embed_text(query)
            candidates = _load_vectors()
            mode = "semantic_query"
            query_payload = query

        ranked = _rank_by_similarity(query_vector, candidates, max_results=max_results)
        response = {
            "mode": mode,
            "query": query_payload,
            "total_results": len(ranked),
            "papers": [
                {
                    "id": paper.paper_id,
                    "title": paper.title,
                    "abstract": paper.abstract,
                    "authors": paper.authors,
                    "categories": paper.categories,
                    "published": paper.published,
                    "score": round(paper.score, 6),
                    "resource_uri": f"arxiv://{paper.paper_id}",
                }
                for paper in ranked
            ],
        }

        return [types.TextContent(type="text", text=json.dumps(response, indent=2))]
    except Exception as exc:
        logger.error("Semantic search failed: %s", exc)
        return [types.TextContent(type="text", text=f"Error: {str(exc)}")]
