"""Save a paper to the personal knowledge base."""

import arxiv
import json
import logging
import time
from typing import Any, Dict, List

import numpy as np
import mcp.types as types

from ..store.knowledge_base import KnowledgeBase
from ..store.knowledge_graph import KnowledgeGraph
from .semantic_search import _load_model, MODEL_NAME

logger = logging.getLogger("arxiv-mcp-server")

kb_save_tool = types.Tool(
    name="kb_save",
    description=(
        "Save a paper to your local knowledge base for later retrieval via kb_search/kb_list. "
        "Supports arXiv (auto-fetches metadata by ID), DOI, or manual entry. Generates an "
        "embedding for semantic search. Optionally add tags, notes, reading status, and collection. "
        "Examples: source=\"arxiv\", source_id=\"2401.12345\" | source=\"arxiv\", source_id=\"1706.03762\", "
        "tags=[\"transformers\"], collection=\"thesis-refs\""
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["arxiv", "doi", "manual"],
                "description": "Paper source: 'arxiv' (auto-fetch metadata), 'doi', or 'manual'.",
            },
            "source_id": {
                "type": "string",
                "description": (
                    "arXiv ID (e.g. '2401.12345') or DOI. "
                    "Required when source is 'arxiv' or 'doi'."
                ),
            },
            "title": {
                "type": "string",
                "description": "Paper title. Required when source is 'manual'.",
            },
            "authors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of author names. Required when source is 'manual'.",
            },
            "abstract": {
                "type": "string",
                "description": "Paper abstract.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subject categories (e.g. ['cs.AI', 'cs.LG']).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "User-defined tags for organization.",
            },
            "notes": {
                "type": "string",
                "description": "Personal notes or annotations.",
            },
            "reading_status": {
                "type": "string",
                "enum": ["unread", "reading", "completed", "archived"],
                "description": "Reading status (default: 'unread').",
            },
            "collection": {
                "type": "string",
                "description": "Add to this collection after saving (creates collection if needed).",
            },
        },
        "required": ["source"],
    },
)


def _fetch_arxiv_paper(source_id: str) -> Dict[str, Any]:
    """Fetch paper metadata from arXiv by ID.

    Args:
        source_id: arXiv paper ID (e.g. '2401.12345').

    Returns:
        Paper metadata dict.

    Raises:
        ValueError: If the paper is not found.
    """
    client = arxiv.Client()
    search = arxiv.Search(id_list=[source_id])

    for result in client.results(search):
        short_id = result.get_short_id()
        return {
            "id": short_id,
            "source": "arxiv",
            "source_id": source_id,
            "title": result.title,
            "authors": [author.name for author in result.authors],
            "abstract": result.summary,
            "categories": result.categories,
            "published_date": result.published.isoformat() if result.published else None,
            "url": result.pdf_url,
        }

    raise ValueError(f"Paper not found on arXiv: {source_id}")


async def _generate_and_store_embedding(
    kb: KnowledgeBase, paper_id: str, title: str, abstract: str | None
) -> bool:
    """Generate and store an embedding for a paper.

    Args:
        kb: KnowledgeBase instance.
        paper_id: Paper ID in the KB.
        title: Paper title.
        abstract: Paper abstract (may be None).

    Returns:
        True if embedding was generated and stored, False otherwise.
    """
    model = _load_model()
    if model is None:
        logger.warning("Embedding model unavailable, skipping embedding generation")
        return False

    text = f"{title} {abstract or ''}"
    embedding = model.encode([text], normalize_embeddings=True)[0]
    embedding_bytes = embedding.astype(np.float32).tobytes()
    await kb.save_embedding(paper_id, MODEL_NAME, embedding_bytes)
    return True


async def handle_kb_save(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle saving a paper to the knowledge base.

    Args:
        arguments: Tool input matching the kb_save schema.

    Returns:
        List with a single TextContent containing saved paper details as JSON.
    """
    try:
        source = arguments["source"]
        source_id = arguments.get("source_id")
        tags = arguments.get("tags", [])
        notes = arguments.get("notes")
        reading_status = arguments.get("reading_status", "unread")
        collection = arguments.get("collection")

        kb = KnowledgeBase()

        # Build paper dict based on source
        if source == "arxiv":
            if not source_id:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: source_id is required when source is 'arxiv'.",
                    )
                ]

            try:
                paper = _fetch_arxiv_paper(source_id)
            except ValueError as e:
                return [types.TextContent(type="text", text=f"Error: {e}")]
            except arxiv.ArxivError as e:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: arXiv API error - {e}",
                    )
                ]

            # Merge user-provided overrides
            if arguments.get("categories"):
                paper["categories"] = arguments["categories"]
            if arguments.get("abstract"):
                paper["abstract"] = arguments["abstract"]

        elif source == "doi":
            if not source_id:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: source_id is required when source is 'doi'.",
                    )
                ]

            paper = {
                "id": f"doi-{source_id.replace('/', '-')}",
                "source": "doi",
                "source_id": source_id,
                "title": arguments.get("title", f"DOI: {source_id}"),
                "authors": arguments.get("authors", []),
                "abstract": arguments.get("abstract"),
                "categories": arguments.get("categories", []),
                "url": f"https://doi.org/{source_id}",
                "notes": "DOI auto-fetch is planned. Metadata was provided manually.",
            }

        elif source == "manual":
            title = arguments.get("title")
            authors = arguments.get("authors")

            if not title:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: title is required when source is 'manual'.",
                    )
                ]
            if not authors:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: authors is required when source is 'manual'.",
                    )
                ]

            paper = {
                "id": f"manual-{int(time.time())}",
                "source": "manual",
                "title": title,
                "authors": authors,
                "abstract": arguments.get("abstract"),
                "categories": arguments.get("categories", []),
            }

        else:
            return [
                types.TextContent(
                    type="text",
                    text=f"Error: Invalid source '{source}'. Must be 'arxiv', 'doi', or 'manual'.",
                )
            ]

        # Apply user annotations
        paper["tags"] = tags
        if notes:
            # Append to existing notes if DOI added a default note
            existing_notes = paper.get("notes")
            if existing_notes:
                paper["notes"] = f"{existing_notes}\n\n{notes}"
            else:
                paper["notes"] = notes
        paper["reading_status"] = reading_status

        # Save to KB
        paper_id = await kb.save_paper(paper)

        # Generate and store embedding
        embedding_stored = await _generate_and_store_embedding(
            kb,
            paper_id,
            paper.get("title", ""),
            paper.get("abstract"),
        )

        # Handle collection
        collection_created = False
        if collection:
            # Create collection if it doesn't exist
            existing_collections = await kb.list_collections()
            collection_names = {c["name"] for c in existing_collections}
            if collection not in collection_names:
                await kb.create_collection(collection)
                collection_created = True
            await kb.add_to_collection(collection, paper_id)

        # Populate knowledge graph (non-blocking — failures don't break saving)
        kg_extracted = False
        try:
            kg = KnowledgeGraph()
            await kg.extract_from_paper(paper)
            kg_extracted = True
        except Exception as kg_err:
            logger.warning(f"Knowledge graph extraction failed for {paper_id}: {kg_err}")

        # Fetch the saved paper to return full details
        saved_paper = await kb.get_paper(paper_id)

        result = {
            "status": "saved",
            "paper": saved_paper,
            "embedding_stored": embedding_stored,
            "kg_extracted": kg_extracted,
        }
        if collection:
            result["collection"] = collection
            result["collection_created"] = collection_created

        return [
            types.TextContent(
                type="text",
                text=json.dumps(result, indent=2),
            )
        ]

    except Exception as e:
        logger.error(f"Unexpected error in kb_save: {e}")
        return [types.TextContent(type="text", text=f"Error: {e}")]
