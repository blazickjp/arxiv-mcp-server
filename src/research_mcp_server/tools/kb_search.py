"""Semantic + keyword search across the personal knowledge base.

All operations are local — no external API calls. Supports keyword-only,
semantic-only (embedding similarity), or hybrid search modes.
"""

import json
import logging
from typing import Any, Dict, List

import numpy as np
import mcp.types as types

from .semantic_search import _load_model, MODEL_NAME, BGE_QUERY_PREFIX
from ..store.knowledge_base import KnowledgeBase

logger = logging.getLogger("research-mcp-server")

kb_search_tool = types.Tool(
    name="kb_search",
    description="""Search your local knowledge base (papers saved via kb_save). Unlike search_papers/arxiv_semantic_search which search arXiv, this searches only YOUR saved papers. Fully local, no API calls.

Modes: "hybrid" (default, best results), "semantic" (meaning-based), "keyword" (text match). Filter by tags, categories, reading_status, or collection.

Examples: query="attention mechanisms" | query="RL for robotics", tags=["agents"], mode="semantic\"""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query.",
                "minLength": 1,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by any of these tags.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by any of these categories (e.g., ['cs.AI', 'cs.LG']).",
            },
            "reading_status": {
                "type": "string",
                "description": "Filter by reading status: unread, reading, completed, archived.",
                "enum": ["unread", "reading", "completed", "archived"],
            },
            "collection": {
                "type": "string",
                "description": "Search within a specific collection.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 10).",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
            "mode": {
                "type": "string",
                "description": "Search mode: 'hybrid' (default), 'semantic', or 'keyword'.",
                "default": "hybrid",
                "enum": ["hybrid", "semantic", "keyword"],
            },
        },
        "required": ["query"],
    },
)


def _apply_filters(
    paper: Dict[str, Any],
    *,
    tags: List[str] | None,
    categories: List[str] | None,
    reading_status: str | None,
) -> bool:
    """Return True if paper passes all provided filters."""
    if reading_status and paper.get("reading_status") != reading_status:
        return False
    if tags:
        paper_tags = paper.get("tags", [])
        if not any(t in paper_tags for t in tags):
            return False
    if categories:
        paper_cats = paper.get("categories", [])
        if not any(c in paper_cats for c in categories):
            return False
    return True


async def handle_kb_search(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle knowledge base search requests.

    Supports keyword, semantic, and hybrid search modes with optional
    tag/category/status/collection filters.

    Args:
        arguments: Tool input with query, optional filters, mode, max_results.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        query = arguments["query"]
        tags = arguments.get("tags")
        categories = arguments.get("categories")
        reading_status = arguments.get("reading_status")
        collection = arguments.get("collection")
        max_results = min(max(int(arguments.get("max_results", 10)), 1), 50)
        mode = arguments.get("mode", "hybrid")

        if mode not in ("hybrid", "semantic", "keyword"):
            mode = "hybrid"

        kb = KnowledgeBase()
        model_note: str | None = None

        # ── Keyword search ──────────────────────────────────────
        keyword_results: List[Dict[str, Any]] = []
        if mode in ("keyword", "hybrid"):
            keyword_results = await kb.list_papers(
                query=query,
                tags=tags,
                categories=categories,
                reading_status=reading_status,
                collection=collection,
                limit=max_results * 5,  # fetch larger pool for hybrid merging
            )

        # ── Semantic search ─────────────────────────────────────
        semantic_results: List[tuple[Dict[str, Any], float]] = []
        if mode in ("semantic", "hybrid"):
            model = _load_model()
            if model is None:
                logger.warning(
                    "Embedding model unavailable, falling back to keyword-only"
                )
                model_note = (
                    "Embedding model could not be loaded. Results are ranked "
                    "by keyword matching only (not semantic similarity)."
                )
                if mode == "semantic":
                    # Pure semantic was requested but model is unavailable —
                    # fall back to keyword
                    keyword_results = await kb.list_papers(
                        query=query,
                        tags=tags,
                        categories=categories,
                        reading_status=reading_status,
                        collection=collection,
                        limit=max_results,
                    )
                    mode = "keyword"
                else:
                    # hybrid — just skip semantic component
                    mode = "keyword"
            else:
                # Get all papers with embeddings
                papers_with_embs = await kb.get_all_papers_with_embeddings(
                    MODEL_NAME
                )

                if papers_with_embs:
                    # Encode query (BGE models need instruction prefix for queries)
                    query_emb = model.encode(
                        [BGE_QUERY_PREFIX + query], normalize_embeddings=True
                    )[0]

                    for paper, emb_bytes in papers_with_embs:
                        emb = np.frombuffer(emb_bytes, dtype=np.float32)
                        sim = float(np.dot(query_emb, emb))

                        # Apply post-ranking filters
                        if not _apply_filters(
                            paper,
                            tags=tags,
                            categories=categories,
                            reading_status=reading_status,
                        ):
                            continue

                        # Collection filter — check if paper is in the collection
                        if collection:
                            paper_collections = paper.get("collections", [])
                            if collection not in paper_collections:
                                continue

                        semantic_results.append((paper, sim))

                    # Sort by similarity descending
                    semantic_results.sort(key=lambda x: x[1], reverse=True)

        # ── Combine results ─────────────────────────────────────
        final_papers: List[Dict[str, Any]]

        if mode == "keyword":
            # Pure keyword or fallback
            final_papers = []
            for paper in keyword_results[:max_results]:
                p = paper.copy()
                p["search_mode"] = "keyword"
                final_papers.append(p)

        elif mode == "semantic":
            # Pure semantic
            final_papers = []
            for paper, sim in semantic_results[:max_results]:
                p = paper.copy()
                p["semantic_score"] = round(sim, 4)
                p["search_mode"] = "semantic"
                final_papers.append(p)

        else:
            # Hybrid — merge keyword and semantic results using
            # Reciprocal Rank Fusion (RRF) with k=60
            rrf_k = 60
            combined_scores: Dict[str, Dict[str, Any]] = {}

            # RRF scores from keyword results (rank starts at 1)
            for rank, paper in enumerate(keyword_results, start=1):
                pid = paper["id"]
                combined_scores[pid] = {
                    "paper": paper,
                    "rrf_score": 1.0 / (rrf_k + rank),
                    "found_by": "keyword",
                }

            # RRF scores from semantic results (rank starts at 1)
            for rank, (paper, _sim) in enumerate(semantic_results, start=1):
                pid = paper["id"]
                rrf_contribution = 1.0 / (rrf_k + rank)
                if pid in combined_scores:
                    combined_scores[pid]["rrf_score"] += rrf_contribution
                    combined_scores[pid]["found_by"] = "both"
                else:
                    combined_scores[pid] = {
                        "paper": paper,
                        "rrf_score": rrf_contribution,
                        "found_by": "semantic",
                    }

            # Sort by RRF score descending
            scored_papers: List[tuple[Dict[str, Any], float, str]] = [
                (entry["paper"], entry["rrf_score"], entry["found_by"])
                for entry in combined_scores.values()
            ]
            scored_papers.sort(key=lambda x: x[1], reverse=True)

            final_papers = []
            for paper, score, found_by in scored_papers[:max_results]:
                p = paper.copy()
                p["rrf_score"] = round(score, 6)
                p["found_by"] = found_by
                p["search_mode"] = "hybrid"
                p["scoring"] = "reciprocal_rank_fusion"
                final_papers.append(p)

        # ── Build response ──────────────────────────────────────
        response: Dict[str, Any] = {
            "total": len(final_papers),
            "mode": mode,
            "query": query,
            "papers": final_papers,
        }

        if model_note:
            response["note"] = model_note

        logger.info(
            f"KB search completed: mode={mode}, query='{query}', "
            f"results={len(final_papers)}"
        )

        return [
            types.TextContent(
                type="text", text=json.dumps(response, indent=2)
            )
        ]

    except Exception as e:
        logger.error(f"KB search error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
