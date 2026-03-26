# CLAUDE.md — arxiv-mcp-server (Enhanced Fork)

## Project Overview

Enhanced fork of [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) (2.3k stars, Python, Apache-2.0). The upstream provides basic arXiv search, download, read, and list tools via MCP (stdio transport). This fork adds an **intelligence layer**: advanced query building, semantic search, citation graph traversal, paper comparison, research digest generation, trend analysis, and structured export.

**Primary consumer**: Claude Code on macOS (M4 MacBook), configured as a local stdio MCP server.
**Secondary consumer**: Claude Desktop, any MCP-compatible client.

## Architecture

```
arxiv-mcp-server/
├── src/arxiv_mcp_server/
│   ├── __init__.py
│   ├── server.py                    # UPSTREAM — main MCP server, tool registration
│   ├── config.py                    # UPSTREAM — configuration (storage path, env vars)
│   │
│   ├── tools/                       # Tool implementations
│   │   ├── __init__.py              # UPSTREAM — tool exports
│   │   ├── search.py                # UPSTREAM — arXiv paper search
│   │   ├── download.py              # UPSTREAM — paper download
│   │   ├── list_papers.py           # UPSTREAM — list stored papers
│   │   ├── read_paper.py            # UPSTREAM — read paper content
│   │   ├── advanced_query.py        # NEW — structured query builder
│   │   ├── semantic_search.py       # NEW — embedding-based similarity search
│   │   ├── compare.py              # NEW — multi-paper comparison
│   │   ├── digest.py               # NEW — research digest generator
│   │   ├── citations.py            # NEW — citation graph via Semantic Scholar API
│   │   ├── trends.py               # NEW — publication trend analysis
│   │   └── export.py               # NEW — BibTeX/markdown/JSON export
│   │
│   ├── clients/                     # NEW — external API clients
│   │   ├── __init__.py
│   │   ├── s2_client.py            # Semantic Scholar API client (httpx, async)
│   │   └── arxiv_client.py         # Thin wrapper over `arxiv` PyPI for advanced queries
│   │
│   ├── store/                       # NEW — local persistence
│   │   ├── __init__.py
│   │   └── sqlite_store.py         # SQLite for paper metadata cache + embeddings
│   │
│   └── utils/                       # NEW — shared utilities
│       ├── __init__.py
│       ├── formatters.py           # Markdown/JSON response formatting
│       └── rate_limiter.py         # Rate limiting for arXiv (3s) and S2 APIs
│
├── tests/                           # Extend with new tool tests
├── CLAUDE.md                        # This file
├── pyproject.toml
└── README.md
```

## Setup

### Prerequisites
- Python 3.11+
- uv (package manager)
- No API keys required for basic usage (arXiv is free, S2 works unauthenticated)
- Optional: `SEMANTIC_SCHOLAR_API_KEY` env var for higher S2 rate limits

### Development Setup
```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[test]"
```

### Running the Server
```bash
python -m arxiv_mcp_server
# or
arxiv-mcp-server
```

### Testing
```bash
python -m pytest                              # all tests with coverage
python -m pytest tests/test_advanced_query.py  # specific test
python -m pytest -v --no-header                # verbose, clean output
```

## Development Commands

```bash
# Format
black src/ tests/

# Run server locally for testing
uv run arxiv-mcp-server --storage-path ~/.arxiv-papers
```

## Claude Code MCP Configuration
```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uv",
      "args": [
        "--directory", "/Users/naman/Code/personal/arxiv-mcp-server",
        "run", "arxiv-mcp-server",
        "--storage-path", "/Users/naman/.arxiv-papers"
      ],
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": ""
      }
    }
  }
}
```

## Implementation Order

### Phase 1: Foundation
1. Directory structure (`tools/`, `clients/`, `store/`, `utils/`) — `__init__.py` files
2. `utils/rate_limiter.py` — token bucket rate limiter
3. `utils/formatters.py` — shared formatting functions
4. `store/sqlite_store.py` — tables, basic CRUD
5. `clients/arxiv_client.py` — thin wrapper for advanced query building

### Phase 2: Core Intelligence Tools
6. `tools/advanced_query.py` — structured query builder
7. `tools/export.py` — BibTeX/markdown/JSON export
8. `clients/s2_client.py` — Semantic Scholar API client with retry logic
9. `tools/citations.py` — citation graph tool

### Phase 3: Semantic & Analysis Tools
10. `tools/semantic_search.py` — embedding search
11. `tools/compare.py` — paper comparison
12. `tools/trends.py` — trend analysis
13. `tools/digest.py` — research digest

### Phase 4: Polish
14. Update `pyproject.toml` with new dependencies
15. Update `README.md`
16. Tests for all new tools
17. End-to-end test in Claude Code

## Tool Registration Pattern

The upstream uses manual `types.Tool` objects + `call_tool()` dispatcher in `server.py`. New tools follow the same pattern:

```python
# In tools/my_tool.py:
my_tool = types.Tool(name="my_tool_name", description="...", inputSchema={...})

async def handle_my_tool(arguments: Dict[str, Any]) -> List[types.TextContent]:
    ...

# In tools/__init__.py: export both
# In server.py: add to list_tools() and call_tool() dispatcher
```

## External APIs

### arXiv API
- Rate limit: max 1 request per 3 seconds
- Base URL: `https://export.arxiv.org/api/query`
- Free, no auth required

### Semantic Scholar API
- Base URL: `https://api.semanticscholar.org/graph/v1`
- Paper ID format: `ArXiv:{arxiv_id}` (strip version suffix)
- Rate limits: 1000 req/s shared (unauthenticated), 1 req/s dedicated (with API key)
- Fields: `paperId,externalIds,title,abstract,year,citationCount,influentialCitationCount,authors,venue,publicationDate,referenceCount,isOpenAccess,fieldsOfStudy`

## Constraints
- **Async everywhere**: All tools must be `async def`. Use `httpx.AsyncClient`, `aiosqlite`.
- **Graceful degradation**: If S2 is down, return arXiv-only data with a note. If embedding model fails, fall back to keyword search.
- **Actionable errors**: If no results, suggest broadening query. If S2 can't find paper, explain ID mapping.
- **No heavy deps**: `sentence-transformers` handles PyTorch. SQLite is sufficient (no vector DB).

## Code Style
- Follow existing codebase conventions (black formatted, Google-style docstrings)
- Type hints on all function signatures
- Pydantic v2 patterns (ConfigDict, Field constraints)
- `json.dumps(result, indent=2)` for JSON tool responses

## Common Mistakes
- Using `asChild` instead of `render={}` — base-ui convention (N/A for Python but noted globally)
- Forgetting to add new tools to BOTH `list_tools()` AND `call_tool()` in server.py
- Forgetting to export new tools in `tools/__init__.py`
- Not respecting arXiv 3-second rate limit
- Using S2 paper ID without `ArXiv:` prefix
- Not stripping version suffix (e.g., `v2`) from arXiv IDs before S2 lookup
