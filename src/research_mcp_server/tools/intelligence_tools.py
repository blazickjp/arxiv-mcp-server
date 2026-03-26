"""Composite CTO intelligence tools.

These meta-tools orchestrate queries across multiple data sources to answer
high-level questions that no single source can answer alone.

Follows the orchestrator-worker pattern from Anthropic's multi-agent research system:
each tool spawns parallel sub-queries internally.
"""

import json
import logging
import asyncio
from typing import Any, Dict, List

import mcp.types as types

logger = logging.getLogger("research-mcp-server")


# =============================================================================
# Tool 1: tech_pulse — "What's trending this week?"
# =============================================================================

tech_pulse_tool = types.Tool(
    name="tech_pulse",
    description=(
        "Get a unified view of what's trending in tech right now. "
        "Aggregates trending content from Hacker News, Reddit dev communities, "
        "GitHub trending repos, Dev.to articles, and HuggingFace papers.\n\n"
        "Use when: 'What are developers talking about this week?', "
        "'What's hot in AI/ML?', 'Any interesting new tools?'"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Optional topic filter. E.g., 'AI', 'Rust', 'web frameworks'. Omit for general tech pulse.",
            },
            "max_per_source": {
                "type": "integer",
                "minimum": 3,
                "maximum": 15,
                "description": "Max items per source. Default: 5.",
            },
        },
    },
)


async def handle_tech_pulse(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Aggregate trending content from multiple sources."""
    # Import handlers lazily to avoid circular imports
    from .hn_tools import handle_hn
    from .community_tools import handle_community
    from .github_tools import handle_github
    from .hf_papers import handle_hf_trending

    topic = arguments.get("topic")
    max_per = arguments.get("max_per_source", 5)
    results: Dict[str, Any] = {}
    errors: List[str] = []

    # Build parallel tasks
    async def fetch_source(name: str, coro):
        try:
            r = await coro
            data = json.loads(r[0].text)
            results[name] = data
        except Exception as e:
            errors.append(f"{name}: {str(e)}")

    tasks = []

    # HN: search if topic, else trending
    if topic:
        tasks.append(fetch_source("hackernews", handle_hn({
            "action": "search", "query": topic, "max_results": max_per, "time_range": "week",
        })))
    else:
        tasks.append(fetch_source("hackernews", handle_hn({
            "action": "trending", "max_results": max_per,
        })))

    # GitHub: trending, optionally filtered
    gh_args: Dict[str, Any] = {"action": "trending", "max_results": max_per, "since": "weekly"}
    if topic:
        gh_args = {"action": "search", "query": topic, "max_results": max_per, "sort": "stars"}
    tasks.append(fetch_source("github", handle_github(gh_args)))

    # Dev.to + Lobsters
    if topic:
        tasks.append(fetch_source("community", handle_community({
            "action": "search", "query": topic, "max_results": max_per,
        })))
    else:
        tasks.append(fetch_source("community", handle_community({
            "action": "trending", "source_filter": "both", "max_results": max_per,
        })))

    # HuggingFace trending (always relevant for AI/ML)
    tasks.append(fetch_source("huggingface", handle_hf_trending({
        "limit": max_per,
    })))

    await asyncio.gather(*tasks)

    output = {
        "topic": topic or "general tech",
        "sources_queried": len(results),
        **results,
    }
    if errors:
        output["errors"] = errors

    return [types.TextContent(type="text", text=json.dumps(output, indent=2))]


# =============================================================================
# Tool 2: evaluate — "Should we use X or Y?"
# =============================================================================

evaluate_tool = types.Tool(
    name="evaluate",
    description=(
        "Compare technologies, tools, or frameworks with evidence from multiple sources. "
        "Pulls GitHub repo stats, package registry data, Reddit discussions, and HN threads "
        "to build a decision matrix.\n\n"
        "Use when: 'Drizzle vs Prisma?', 'Should we use Bun or pnpm?', "
        "'FastAPI vs Litestar for our API?'"
    ),
    inputSchema={
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 5,
                "description": "Technologies to compare. E.g., ['Drizzle', 'Prisma', 'Kysely'].",
            },
            "github_repos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional GitHub repos in owner/repo format. Auto-detected if omitted.",
            },
            "package_names": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "registry": {"type": "string", "enum": ["npm", "pypi", "crates"]},
                    },
                    "required": ["name", "registry"],
                },
                "description": "Optional package names + registries. Auto-searched if omitted.",
            },
        },
    },
)


async def handle_evaluate(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Compare technologies using multiple data sources."""
    from .github_tools import handle_github
    from .reddit_tools import handle_reddit
    from .hn_tools import handle_hn
    from .package_tools import handle_packages

    items = arguments["items"]
    results: Dict[str, Any] = {"items": items}
    errors: List[str] = []

    async def safe_call(name, coro):
        try:
            r = await coro
            return json.loads(r[0].text)
        except Exception as e:
            errors.append(f"{name}: {str(e)}")
            return None

    # 1. GitHub comparison (if repos provided)
    github_repos = arguments.get("github_repos")
    if github_repos and len(github_repos) >= 2:
        gh_data = await safe_call("github", handle_github({
            "action": "compare", "repos": github_repos,
        }))
        if gh_data:
            results["github"] = gh_data

    # 2. Search GitHub for each item
    if not github_repos:
        gh_results = []
        for item in items:
            data = await safe_call(f"github:{item}", handle_github({
                "action": "search", "query": item, "max_results": 3,
            }))
            if data:
                gh_results.append({"item": item, "top_repos": data.get("repos", [])[:3]})
        if gh_results:
            results["github_search"] = gh_results

    # 3. Package stats (if provided)
    pkg_names = arguments.get("package_names")
    if pkg_names:
        pkg_data = await safe_call("packages", handle_packages({
            "action": "compare", "packages": pkg_names,
        }))
        if pkg_data:
            results["packages"] = pkg_data

    # 4. Reddit discussions — search for comparison threads
    vs_query = " vs ".join(items[:3])
    reddit_data = await safe_call("reddit", handle_reddit({
        "action": "search", "query": vs_query, "max_results": 5, "time_filter": "year",
    }))
    if reddit_data:
        results["reddit_discussions"] = reddit_data

    # 5. HN discussions
    hn_data = await safe_call("hackernews", handle_hn({
        "action": "search", "query": vs_query, "max_results": 5, "time_range": "year",
    }))
    if hn_data:
        results["hn_discussions"] = hn_data

    if errors:
        results["errors"] = errors

    return [types.TextContent(type="text", text=json.dumps(results, indent=2))]


# =============================================================================
# Tool 3: sentiment — "What does the community think about X?"
# =============================================================================

sentiment_tool = types.Tool(
    name="sentiment",
    description=(
        "Analyze community sentiment about a technology, tool, or topic. "
        "Gathers discussions from Reddit and Hacker News, then summarizes "
        "the overall sentiment with key praise, concerns, and representative quotes.\n\n"
        "Use when: 'What do devs think about Bun?', 'Is LangChain liked or hated?', "
        "'Community opinion on Tailwind v4?'"
    ),
    inputSchema={
        "type": "object",
        "required": ["topic"],
        "properties": {
            "topic": {
                "type": "string",
                "description": "Technology or topic to analyze sentiment for.",
            },
            "subreddits": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific subreddits to search. Default: broad dev subreddits.",
            },
            "time_range": {
                "type": "string",
                "enum": ["week", "month", "year"],
                "description": "How far back to look. Default: month.",
            },
            "max_threads": {
                "type": "integer",
                "minimum": 3,
                "maximum": 15,
                "description": "Max discussion threads to analyze. Default: 8.",
            },
        },
    },
)


async def handle_sentiment(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Gather community discussions and summarize sentiment."""
    from .reddit_tools import handle_reddit
    from .hn_tools import handle_hn

    topic = arguments["topic"]
    time_range = arguments.get("time_range", "month")
    max_threads = arguments.get("max_threads", 8)
    errors: List[str] = []

    discussions: Dict[str, Any] = {"topic": topic}

    # Fetch Reddit threads
    try:
        reddit_result = await handle_reddit({
            "action": "search",
            "query": topic,
            "sort": "relevance",
            "time_filter": time_range,
            "max_results": max_threads,
        })
        reddit_data = json.loads(reddit_result[0].text)
        discussions["reddit"] = {
            "total_posts": reddit_data.get("total", 0),
            "posts": reddit_data.get("posts", []),
        }
    except Exception as e:
        errors.append(f"reddit: {str(e)}")

    # Fetch HN threads
    try:
        hn_result = await handle_hn({
            "action": "search",
            "query": topic,
            "sort": "relevance",
            "time_range": time_range if time_range != "month" else "year",  # HN has different ranges
            "max_results": max_threads,
        })
        hn_data = json.loads(hn_result[0].text)
        discussions["hackernews"] = {
            "total_stories": hn_data.get("total", 0),
            "stories": hn_data.get("stories", []),
        }
    except Exception as e:
        errors.append(f"hackernews: {str(e)}")

    # Build summary metrics
    reddit_posts = discussions.get("reddit", {}).get("posts", [])
    hn_stories = discussions.get("hackernews", {}).get("stories", [])

    total_engagement = (
        sum(p.get("score", 0) for p in reddit_posts) +
        sum(s.get("points", 0) for s in hn_stories)
    )
    total_comments = (
        sum(p.get("num_comments", 0) for p in reddit_posts) +
        sum(s.get("num_comments", 0) for s in hn_stories)
    )

    discussions["summary"] = {
        "total_threads_found": len(reddit_posts) + len(hn_stories),
        "total_engagement_score": total_engagement,
        "total_comments": total_comments,
        "avg_engagement": round(total_engagement / max(len(reddit_posts) + len(hn_stories), 1), 1),
        "note": (
            "Review the posts and comments above for qualitative sentiment. "
            "High engagement + high upvote ratios = positive sentiment. "
            "Use the 'discussion' action on reddit/hn tools to read individual thread comments."
        ),
    }

    if errors:
        discussions["errors"] = errors

    return [types.TextContent(type="text", text=json.dumps(discussions, indent=2))]


# =============================================================================
# Tool 4: deep_research — "Everything about X"
# =============================================================================

deep_research_tool = types.Tool(
    name="deep_research",
    description=(
        "Comprehensive research on a topic combining academic papers AND practitioner "
        "perspectives. Queries arXiv, GitHub, HN, Reddit, Dev.to, and package registries "
        "to build a complete picture.\n\n"
        "Use when: 'Everything about WebTransport', 'Full picture on vector databases', "
        "'Research report on RLHF techniques'"
    ),
    inputSchema={
        "type": "object",
        "required": ["topic"],
        "properties": {
            "topic": {
                "type": "string",
                "description": "Research topic. Be specific for better results.",
            },
            "max_per_source": {
                "type": "integer",
                "minimum": 3,
                "maximum": 10,
                "description": "Max results per source. Default: 5.",
            },
            "include_packages": {
                "type": "boolean",
                "description": "Search package registries for related packages. Default: true.",
            },
        },
    },
)


async def handle_deep_research(arguments: Dict[str, Any]) -> List[types.TextContent]:
    """Comprehensive multi-source research."""
    from .search import handle_search
    from .github_tools import handle_github
    from .hn_tools import handle_hn
    from .reddit_tools import handle_reddit
    from .community_tools import handle_community
    from .package_tools import handle_packages

    topic = arguments["topic"]
    max_per = arguments.get("max_per_source", 5)
    include_packages = arguments.get("include_packages", True)
    results: Dict[str, Any] = {"topic": topic}
    errors: List[str] = []

    async def safe_call(name, coro):
        try:
            r = await coro
            return json.loads(r[0].text)
        except Exception as e:
            errors.append(f"{name}: {str(e)}")
            return None

    # Run all sources in parallel
    tasks = {
        "arxiv": safe_call("arxiv", handle_search({
            "query": topic, "max_results": max_per, "sort_by": "relevance",
        })),
        "github": safe_call("github", handle_github({
            "action": "search", "query": topic, "max_results": max_per,
        })),
        "hackernews": safe_call("hackernews", handle_hn({
            "action": "search", "query": topic, "max_results": max_per, "time_range": "year",
        })),
        "reddit": safe_call("reddit", handle_reddit({
            "action": "search", "query": topic, "max_results": max_per, "time_filter": "year",
        })),
        "community": safe_call("community", handle_community({
            "action": "search", "query": topic, "max_results": max_per,
        })),
    }

    if include_packages:
        tasks["packages_npm"] = safe_call("packages_npm", handle_packages({
            "action": "search", "query": topic, "search_registry": "npm", "max_results": 3,
        }))

    # Execute all in parallel
    task_names = list(tasks.keys())
    task_coros = list(tasks.values())
    task_results = await asyncio.gather(*task_coros)

    for name, data in zip(task_names, task_results):
        if data is not None:
            results[name] = data

    if errors:
        results["errors"] = errors

    results["sources_queried"] = len(task_names)
    results["sources_succeeded"] = len(task_names) - len(errors)

    return [types.TextContent(type="text", text=json.dumps(results, indent=2))]
