"""Package registry tool — npm, PyPI, crates.io stats and comparison."""

import json
import logging
import asyncio
from typing import Any, Dict, List

import mcp.types as types

from ..clients.package_client import PackageClient

logger = logging.getLogger("research-mcp-server")

packages_tool = types.Tool(
    name="packages",
    description=(
        "Get package stats, compare packages, and search registries. "
        "Covers npm, PyPI, and crates.io.\n"
        "Actions:\n"
        "- 'stats': Get detailed info for a package (version, downloads, license, repo).\n"
        "- 'compare': Compare 2+ packages side-by-side (same or different registries).\n"
        "- 'search': Search for packages by keyword."
    ),
    inputSchema={
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["stats", "compare", "search"],
                "description": "The operation to perform.",
            },
            "name": {
                "type": "string",
                "description": "Package name (for 'stats'). E.g., 'express', 'fastapi', 'tokio'.",
            },
            "registry": {
                "type": "string",
                "enum": ["npm", "pypi", "crates"],
                "description": "Which registry (for 'stats'). Default: auto-detect.",
            },
            "packages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "registry": {
                            "type": "string",
                            "enum": ["npm", "pypi", "crates"],
                        },
                    },
                    "required": ["name", "registry"],
                },
                "description": (
                    "Packages to compare (for 'compare'). "
                    'E.g., [{"name": "express", "registry": "npm"}, '
                    '{"name": "fastify", "registry": "npm"}].'
                ),
            },
            "query": {
                "type": "string",
                "description": "Search query (for 'search').",
            },
            "search_registry": {
                "type": "string",
                "enum": ["npm", "crates"],
                "description": "Registry to search (for 'search'). Currently supports npm and crates.io. Default: npm.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Max results for search. Default: 10.",
            },
        },
    },
)


async def _detect_registry(name: str) -> str:
    """Try to auto-detect which registry a package belongs to."""
    client = PackageClient()
    # Try PyPI first (most Python users), then npm, then crates
    for registry, method in [
        ("pypi", client.get_pypi),
        ("npm", client.get_npm),
        ("crates", client.get_crate),
    ]:
        try:
            await method(name)
            return registry
        except Exception:
            continue
    return "npm"  # Default fallback


async def handle_packages(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Dispatch package tool calls."""
    action = arguments.get("action")
    if not action:
        return [types.TextContent(type="text", text="Error: 'action' is required.")]

    client = PackageClient()

    try:
        if action == "stats":
            name = arguments.get("name")
            if not name:
                return [
                    types.TextContent(
                        type="text", text="Error: 'name' is required for stats."
                    )
                ]
            registry = arguments.get("registry")
            if not registry:
                registry = await _detect_registry(name)
            result = await client.get_package(name, registry)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif action == "compare":
            packages_list = arguments.get("packages")
            if not packages_list or len(packages_list) < 2:
                return [
                    types.TextContent(
                        type="text",
                        text="Error: 'packages' array with 2+ items required for compare.",
                    )
                ]

            results = []
            for pkg in packages_list:
                try:
                    info = await client.get_package(pkg["name"], pkg["registry"])
                    results.append(info)
                except Exception as e:
                    results.append(
                        {
                            "name": pkg["name"],
                            "registry": pkg["registry"],
                            "error": str(e),
                        }
                    )

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"comparison": results}, indent=2),
                )
            ]

        elif action == "search":
            query = arguments.get("query")
            if not query:
                return [
                    types.TextContent(
                        type="text", text="Error: 'query' is required for search."
                    )
                ]
            search_registry = arguments.get("search_registry", "npm")
            max_results = arguments.get("max_results", 10)

            if search_registry == "npm":
                results = await client.search_npm(query, max_results)
            elif search_registry == "crates":
                results = await client.search_crates(query, max_results)
            else:
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error: Search not supported for '{search_registry}'. Use 'npm' or 'crates'.",
                    )
                ]

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "total": len(results),
                            "registry": search_registry,
                            "packages": results,
                        },
                        indent=2,
                    ),
                )
            ]

        else:
            return [
                types.TextContent(
                    type="text", text=f"Error: Unknown action '{action}'."
                )
            ]

    except Exception as e:
        logger.error(f"Packages tool error: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]
