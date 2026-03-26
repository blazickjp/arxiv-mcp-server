"""Query the knowledge graph for papers, concepts, methods, datasets, and authors."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import mcp.types as types

from ..store.knowledge_graph import KnowledgeGraph

logger = logging.getLogger("research-mcp-server")

kg_query_tool = types.Tool(
    name="kg_query",
    description=(
        "Query the research knowledge graph. Find papers, concepts, methods, "
        "datasets, and authors, and explore relationships between them. "
        "Supports natural language queries, filtered searches, subgraph "
        "exploration, and graph statistics."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language query, e.g. 'papers using attention on "
                    "biomedical NER', 'methods for object detection', "
                    "'datasets in NLP'."
                ),
            },
            "node_type": {
                "type": "string",
                "enum": ["paper", "concept", "method", "dataset", "author"],
                "description": "Filter by node type.",
            },
            "relation": {
                "type": "string",
                "enum": [
                    "cites", "uses_method", "evaluates_on",
                    "extends", "authored_by", "related_to",
                ],
                "description": "Filter by relation type.",
            },
            "connected_to": {
                "type": "string",
                "description": "Find nodes connected to this node ID.",
            },
            "center_id": {
                "type": "string",
                "description": "Get subgraph around this node ID.",
            },
            "hops": {
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 3,
                "description": "Number of hops for subgraph traversal (1-3).",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "maximum": 100,
                "description": "Maximum number of results.",
            },
            "show_stats": {
                "type": "boolean",
                "default": False,
                "description": "Return graph statistics instead of query results.",
            },
        },
    },
)


async def handle_kg_query(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Handle a knowledge graph query.

    Args:
        arguments: Tool input matching the kg_query schema.

    Returns:
        List with a single TextContent containing JSON results.
    """
    try:
        kg = KnowledgeGraph()

        query_text: Optional[str] = arguments.get("query")
        node_type: Optional[str] = arguments.get("node_type")
        relation: Optional[str] = arguments.get("relation")
        connected_to: Optional[str] = arguments.get("connected_to")
        center_id: Optional[str] = arguments.get("center_id")
        hops: int = arguments.get("hops", 2)
        limit: int = min(arguments.get("limit", 20), 100)
        show_stats: bool = arguments.get("show_stats", False)

        # Stats mode
        if show_stats:
            stats = await kg.get_stats()
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"stats": stats}, indent=2),
                )
            ]

        # Subgraph mode
        if center_id:
            subgraph = await kg.get_subgraph(center_id, hops=hops)
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "center_id": center_id,
                        "hops": hops,
                        "node_count": len(subgraph["nodes"]),
                        "edge_count": len(subgraph["edges"]),
                        "subgraph": subgraph,
                    }, indent=2),
                )
            ]

        # Natural language query mode
        if query_text:
            parsed = _parse_query(query_text)
            # Override with explicit params if provided
            effective_type = node_type or parsed.get("node_type")
            effective_relation = relation or parsed.get("relation")
            effective_connected_to = connected_to or parsed.get("connected_to")
            label_search = parsed.get("keyword")

            # If we have a connected_to from parsing, try to find the node ID
            if effective_connected_to and not connected_to:
                effective_connected_to = await _resolve_node_id(
                    kg, effective_connected_to, parsed.get("connected_type")
                )

            if effective_connected_to:
                nodes = await kg.query(
                    node_type=effective_type,
                    relation=effective_relation,
                    connected_to=effective_connected_to,
                    label_contains=label_search,
                    limit=limit,
                )
            else:
                # Fall back to label search
                nodes = await kg.query(
                    node_type=effective_type,
                    label_contains=label_search or query_text,
                    limit=limit,
                )

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "query": query_text,
                        "parsed": parsed,
                        "count": len(nodes),
                        "nodes": nodes,
                    }, indent=2),
                )
            ]

        # Explicit filter mode
        nodes = await kg.query(
            node_type=node_type,
            relation=relation,
            connected_to=connected_to,
            limit=limit,
        )

        return [
            types.TextContent(
                type="text",
                text=json.dumps({
                    "count": len(nodes),
                    "nodes": nodes,
                }, indent=2),
            )
        ]

    except Exception as e:
        logger.error(f"Error in kg_query: {e}")
        return [types.TextContent(type="text", text=f"Error: {e}")]


def _parse_query(query: str) -> dict[str, Any]:
    """Parse a natural language query into structured filters.

    Recognizes patterns like:
    - "papers using X" -> node_type=paper, relation=uses_method, keyword=X
    - "methods for X" -> node_type=method, connected to concept X
    - "datasets in X" -> node_type=dataset, label search
    - "papers by X" -> node_type=paper, relation=authored_by, keyword=X

    Returns:
        Dict with parsed fields: node_type, relation, keyword,
        connected_to, connected_type.
    """
    result: dict[str, Any] = {}
    q = query.strip()

    # "papers using X" / "papers that use X"
    match = re.match(
        r"papers?\s+(?:using|that\s+use|employing)\s+(.+)",
        q, re.IGNORECASE,
    )
    if match:
        result["node_type"] = "paper"
        result["relation"] = "uses_method"
        result["keyword"] = match.group(1).strip()
        result["connected_to"] = match.group(1).strip()
        result["connected_type"] = "method"
        return result

    # "papers on X" / "papers about X"
    match = re.match(
        r"papers?\s+(?:on|about|related\s+to)\s+(.+)",
        q, re.IGNORECASE,
    )
    if match:
        result["node_type"] = "paper"
        result["relation"] = "related_to"
        result["keyword"] = match.group(1).strip()
        result["connected_to"] = match.group(1).strip()
        result["connected_type"] = "concept"
        return result

    # "papers by X"
    match = re.match(
        r"papers?\s+by\s+(.+)",
        q, re.IGNORECASE,
    )
    if match:
        result["node_type"] = "paper"
        result["relation"] = "authored_by"
        result["keyword"] = match.group(1).strip()
        result["connected_to"] = match.group(1).strip()
        result["connected_type"] = "author"
        return result

    # "methods for X"
    match = re.match(
        r"methods?\s+(?:for|in|used\s+in)\s+(.+)",
        q, re.IGNORECASE,
    )
    if match:
        result["node_type"] = "method"
        result["keyword"] = match.group(1).strip()
        result["connected_to"] = match.group(1).strip()
        result["connected_type"] = "concept"
        return result

    # "datasets for X" / "datasets in X"
    match = re.match(
        r"datasets?\s+(?:for|in|used\s+in)\s+(.+)",
        q, re.IGNORECASE,
    )
    if match:
        result["node_type"] = "dataset"
        result["keyword"] = match.group(1).strip()
        return result

    # "authors of X" / "who wrote X"
    match = re.match(
        r"(?:authors?\s+of|who\s+wrote)\s+(.+)",
        q, re.IGNORECASE,
    )
    if match:
        result["node_type"] = "author"
        result["keyword"] = match.group(1).strip()
        return result

    # Fallback: use entire query as keyword
    result["keyword"] = q
    return result


async def _resolve_node_id(
    kg: KnowledgeGraph,
    label_hint: str,
    expected_type: Optional[str] = None,
) -> Optional[str]:
    """Try to find a node ID from a label hint.

    Searches for nodes whose label contains the hint text.
    Returns the first matching node's ID, or None.
    """
    nodes = await kg.query(
        node_type=expected_type,
        label_contains=label_hint,
        limit=1,
    )
    if nodes:
        return nodes[0]["id"]
    return None
