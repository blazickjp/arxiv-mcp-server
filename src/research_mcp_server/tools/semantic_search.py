"""Embedding-based semantic search over arXiv results."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import numpy as np
import mcp.types as types

from ..store.sqlite_store import SQLiteStore
from ..tools.search import _raw_arxiv_search

logger = logging.getLogger("research-mcp-server")

# NOTE: Upgraded from sentence-transformers/all-MiniLM-L6-v2 to BAAI/bge-small-en-v1.5
# for better performance on scientific text. Both are 384-dim, but embeddings are NOT
# compatible — any cached embeddings from the old model must be regenerated.
MODEL_NAME = "BAAI/bge-small-en-v1.5"

# BGE models perform best when queries (not documents) are prefixed with this instruction.
BGE_QUERY_PREFIX = "Represent this sentence: "

# Lazy-loaded model — only initialized on first use
_model: Optional[Any] = None

# Stopwords for query decomposition
_QUERY_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "not", "no", "so", "if",
    "then", "than", "too", "very", "just", "also", "more", "most", "some",
    "such", "only", "how", "what", "which", "who", "when", "where", "why",
    "there", "here", "i", "we", "they", "it", "its", "this", "that",
    "these", "those", "my", "your", "our", "their",
}

# Connectors used to split queries into noun phrases
_QUERY_CONNECTORS = re.compile(
    r"\b(?:for|with|using|about|how to|that|which|and|or|in|on|of|by)\b",
    re.IGNORECASE,
)


def _decompose_query(query: str, model: Any) -> List[str]:
    """Decompose a natural language query into 2-4 core scientific concepts.

    Splits the query on common connectors, removes stopwords from each phrase,
    and keeps phrases that are 1-4 words long.

    Args:
        query: Natural language research query.
        model: Loaded SentenceTransformer model (unused currently, reserved for
            future model-based decomposition).

    Returns:
        List of concept strings. Falls back to [query] if fewer than 2 concepts
        are extracted.
    """
    # Split on connectors
    fragments = _QUERY_CONNECTORS.split(query)

    concepts: List[str] = []
    for fragment in fragments:
        # Clean and remove stopwords
        words = fragment.strip().split()
        cleaned_words = [
            w for w in words
            if w.lower().strip(".,;:!?()") not in _QUERY_STOPWORDS
            and len(w.strip(".,;:!?()")) > 1
        ]
        if not cleaned_words:
            continue

        phrase = " ".join(w.strip(".,;:!?()") for w in cleaned_words)
        # Keep phrases that are 1-4 words long
        word_count = len(cleaned_words)
        if 1 <= word_count <= 4 and phrase:
            concepts.append(phrase)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_concepts: List[str] = []
    for c in concepts:
        c_lower = c.lower()
        if c_lower not in seen:
            seen.add(c_lower)
            unique_concepts.append(c)

    # Cap at 4 concepts
    unique_concepts = unique_concepts[:4]

    # Fall back to original query if fewer than 2 concepts
    if len(unique_concepts) < 2:
        return [query]

    return unique_concepts


def _load_model() -> Any:
    """Lazy-load the sentence-transformers model.

    Returns:
        Loaded SentenceTransformer model, or None if loading fails.
    """
    global _model
    if _model is not None:
        return _model

    try:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model: {MODEL_NAME}")
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Embedding model loaded successfully")
        return _model
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}")
        return None


semantic_search_tool = types.Tool(
    name="semantic_search",
    description="""Meaning-based arXiv search using embeddings (BAAI/bge-small-en-v1.5). Use when keyword search (search_papers) misses relevant papers due to different terminology, or when you want to find conceptually similar work. Searches arXiv by keyword first, then re-ranks by semantic similarity.

Unlike search_papers (exact keyword matching) or arxiv_advanced_query (structured fields), this finds papers by meaning. Max 30 results from a pool of up to 200. Falls back to keyword-only if embedding model is unavailable.

Examples: query="how do language models reason about math" | query="efficient training of large neural networks", categories=["cs.LG"]""",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language research query describing the topic or concept to search for.",
                "minLength": 3,
                "maxLength": 500,
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arXiv categories to filter (e.g., ['cs.AI', 'cs.LG']).",
            },
            "max_results": {
                "type": "integer",
                "description": "Number of semantically top-ranked results to return (default: 10, max: 30).",
                "default": 10,
                "minimum": 1,
                "maximum": 30,
            },
            "search_pool_size": {
                "type": "integer",
                "description": "Size of the initial keyword search pool to re-rank (default: 100, max: 200). Larger pools find more diverse results but take longer.",
                "default": 100,
                "minimum": 20,
                "maximum": 200,
            },
            "date_from": {
                "type": "string",
                "description": "Only include papers published after this date (YYYY-MM-DD).",
            },
        },
        "required": ["query"],
    },
)


async def handle_semantic_search(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle embedding-based semantic search requests.

    Fetches a broad pool of keyword results from arXiv, encodes them with a
    sentence-transformer model, and re-ranks by cosine similarity to the query.
    Embeddings are cached in SQLite for subsequent searches.

    Args:
        arguments: Tool input with query, optional categories, max_results,
            search_pool_size, and date_from.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        query = arguments["query"]
        categories = arguments.get("categories")
        max_results = min(int(arguments.get("max_results", 10)), 30)
        search_pool_size = min(int(arguments.get("search_pool_size", 100)), 200)
        search_pool_size = max(search_pool_size, 20)
        date_from = arguments.get("date_from")

        logger.info(
            f"Semantic search: query='{query}', pool={search_pool_size}, "
            f"max_results={max_results}"
        )

        # Step 1: Broad keyword search to build candidate pool
        try:
            pool = await _raw_arxiv_search(
                query=query,
                max_results=search_pool_size,
                sort_by="relevance",
                date_from=date_from,
                categories=categories,
            )
        except Exception as e:
            logger.error(f"arXiv search failed: {e}")
            return [
                types.TextContent(
                    type="text",
                    text=f"Error: arXiv search failed - {str(e)}",
                )
            ]

        if not pool:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "total_results": 0,
                            "papers": [],
                            "note": "No papers found matching the query.",
                        },
                        indent=2,
                    ),
                )
            ]

        # Step 2: Try to load embedding model
        model = _load_model()

        if model is None:
            # Fallback: return keyword-only results with a note
            logger.warning("Embedding model unavailable, falling back to keyword search")
            fallback_papers = pool[:max_results]
            response = {
                "total_results": len(fallback_papers),
                "papers": fallback_papers,
                "note": (
                    "Embedding model could not be loaded. Results are ranked by "
                    "arXiv keyword relevance only (not semantic similarity)."
                ),
            }
            return [
                types.TextContent(
                    type="text", text=json.dumps(response, indent=2)
                )
            ]

        # Step 3: Encode query and paper texts, using cached embeddings where possible
        store = SQLiteStore()

        # Build text for each paper
        paper_texts = []
        paper_ids = []
        for paper in pool:
            text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
            paper_texts.append(text)
            paper_ids.append(paper.get("id", ""))

        # Check cache for existing embeddings
        cached_embeddings: Dict[str, np.ndarray] = {}
        uncached_indices: List[int] = []

        for i, paper_id in enumerate(paper_ids):
            if not paper_id:
                uncached_indices.append(i)
                continue
            cached_bytes = await store.get_embedding(paper_id, MODEL_NAME)
            if cached_bytes is not None:
                cached_embeddings[paper_id] = np.frombuffer(
                    cached_bytes, dtype=np.float32
                )
            else:
                uncached_indices.append(i)

        # Encode uncached papers
        if uncached_indices:
            uncached_texts = [paper_texts[i] for i in uncached_indices]
            logger.debug(
                f"Encoding {len(uncached_texts)} uncached paper embeddings"
            )
            paper_embs = model.encode(
                uncached_texts, normalize_embeddings=True
            )

            # Cache new embeddings
            for idx, emb_idx in enumerate(uncached_indices):
                pid = paper_ids[emb_idx]
                emb_vector = paper_embs[idx]
                cached_embeddings[pid] = emb_vector
                if pid:
                    await store.upsert_embedding(
                        pid, MODEL_NAME, emb_vector.astype(np.float32).tobytes()
                    )

        # Decompose query into concepts for multi-vector scoring
        concepts = _decompose_query(query, model)
        logger.debug(f"Query decomposed into {len(concepts)} concepts: {concepts}")

        # Encode each concept (or the full query if only 1 concept)
        concept_embeddings = []
        for concept in concepts:
            emb = model.encode(
                [BGE_QUERY_PREFIX + concept], normalize_embeddings=True
            )[0]
            concept_embeddings.append(emb)

        # Step 4: Compute cosine similarity — use MAX across concept embeddings
        similarities: List[tuple[int, float]] = []
        for i, paper in enumerate(pool):
            pid = paper_ids[i]
            if pid in cached_embeddings:
                paper_emb = cached_embeddings[pid]
                sim = max(
                    float(np.dot(c_emb, paper_emb))
                    for c_emb in concept_embeddings
                )
            else:
                sim = 0.0
            similarities.append((i, sim))

        # Step 5: Sort by similarity and take top results
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_indices = similarities[:max_results]

        ranked_papers = []
        for idx, sim_score in top_indices:
            paper = pool[idx].copy()
            paper["semantic_similarity"] = round(sim_score, 4)
            ranked_papers.append(paper)

        response = {
            "total_results": len(ranked_papers),
            "search_pool_size": len(pool),
            "model": MODEL_NAME,
            "papers": ranked_papers,
        }

        logger.info(
            f"Semantic search completed: {len(ranked_papers)} results "
            f"from pool of {len(pool)}"
        )

        return [
            types.TextContent(
                type="text", text=json.dumps(response, indent=2)
            )
        ]

    except Exception as e:
        logger.error(f"Unexpected semantic search error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
