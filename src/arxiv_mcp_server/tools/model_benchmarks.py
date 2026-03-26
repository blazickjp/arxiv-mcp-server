"""MCP tool for AI model benchmark comparisons using Epoch AI data."""

import json
import logging
from typing import Any, Dict, List

import mcp.types as types

from ..clients.epoch_client import EpochClient

logger = logging.getLogger("arxiv-mcp-server")


model_benchmarks_tool = types.Tool(
    name="model_benchmarks",
    description="""Search and compare AI model benchmarks using Epoch AI's public dataset of notable AI models and benchmark runs.

Actions:
- "search_models": Search Epoch's catalog of notable AI models by name, organization, or domain.
- "compare": Side-by-side benchmark comparison of specific models (provide model_names).
- "benchmarks": Search benchmark results, optionally filtered by model or benchmark name.

Examples:
  action="search_models", query="GPT" | action="compare", model_names=["GPT-4", "Claude 3"] | action="benchmarks", benchmark="MMLU"
""",
    inputSchema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search_models", "compare", "benchmarks"],
                "description": "Action to perform: search_models, compare, or benchmarks.",
            },
            "query": {
                "type": "string",
                "description": "Model name, organization, or search term. Used with 'search_models' and 'benchmarks' actions.",
            },
            "model_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of model names for 'compare' action.",
            },
            "benchmark": {
                "type": "string",
                "description": "Filter by specific benchmark name (e.g., 'MMLU', 'HumanEval'). Used with 'benchmarks' action.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of results to return. Default: 10.",
            },
        },
        "required": ["action"],
    },
)


async def handle_model_benchmarks(
    arguments: Dict[str, Any],
) -> List[types.TextContent]:
    """Handle model benchmark requests by routing to the appropriate Epoch AI query.

    Args:
        arguments: Tool input arguments.

    Returns:
        List containing a single TextContent with JSON results.
    """
    try:
        action = arguments["action"]
        query = arguments.get("query")
        model_names = arguments.get("model_names", [])
        benchmark = arguments.get("benchmark")
        limit = min(max(arguments.get("limit", 10), 1), 100)

        client = EpochClient()

        if action == "search_models":
            results = await client.get_models(query=query, limit=limit)
            response = {
                "action": "search_models",
                "query": query,
                "count": len(results),
                "models": results,
            }

        elif action == "compare":
            if not model_names:
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "error": "missing_parameter",
                                "message": "The 'compare' action requires 'model_names' (a list of model names to compare).",
                            },
                            indent=2,
                        ),
                    )
                ]
            comparison = await client.compare_models(model_names)
            response = {
                "action": "compare",
                "model_names": model_names,
                "comparison": comparison,
            }

        elif action == "benchmarks":
            results = await client.get_benchmark_runs(
                model=query,
                benchmark=benchmark,
                limit=limit,
            )
            response = {
                "action": "benchmarks",
                "query": query,
                "benchmark_filter": benchmark,
                "count": len(results),
                "benchmark_runs": results,
            }

        else:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "invalid_action",
                            "message": f"Unknown action '{action}'. Use 'search_models', 'compare', or 'benchmarks'.",
                        },
                        indent=2,
                    ),
                )
            ]

        return [
            types.TextContent(type="text", text=json.dumps(response, indent=2))
        ]

    except Exception as e:
        logger.error(f"Model benchmarks error: {e}")
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {"error": "model_benchmarks_error", "message": str(e)},
                    indent=2,
                ),
            )
        ]
