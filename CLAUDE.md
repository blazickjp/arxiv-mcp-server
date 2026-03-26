# CLAUDE.md — research-mcp-server v2

## Project Overview

Multi-source research intelligence server via MCP (stdio transport). Started as a fork of [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) — now expanded to **31 tools across 16 data sources** covering academic papers, developer communities, package registries, and composite "CTO intelligence" analysis.

**Primary consumer**: Claude Code on macOS, configured as a local stdio MCP server.
**Secondary consumer**: Claude Desktop, any MCP-compatible client.

## Tool Inventory (31 tools)

### Search & Discovery (3)
| Tool | Description |
|------|-------------|
| `search` | Unified arXiv search — keyword/phrase + structured field-by-field |
| `semantic_search` | Embedding-based similarity search (BAAI/bge-small-en-v1.5) |
| `cross_search` | Parallel search across arXiv, OpenAlex, Crossref with dedup |

### Paper Management (4)
| Tool | Description |
|------|-------------|
| `download_paper` | Download PDF, convert to markdown |
| `list_papers` | List downloaded papers |
| `read_paper` | Read full paper content |
| `read_paper_chunks` | Read paper by structured sections |

### Analysis (5)
| Tool | Description |
|------|-------------|
| `citations` | Citation graph + optional structural analysis (`analyze=true`) |
| `lineage` | Intellectual influence DAG (depth 3) |
| `compare` | Side-by-side paper comparison |
| `trends` | Publication trend analysis |
| `digest` | Research area summaries |

### Knowledge & Memory (3)
| Tool | Description |
|------|-------------|
| `kb` | Unified knowledge base (`action`: save/search/list/annotate/remove) |
| `kg_query` | Knowledge graph queries |
| `memory` | Session tracking + persistent research memory (`action`: create/status/log_paper/add_thesis/warm_context/...) |

### Academic Sources (6)
| Tool | Source | Description |
|------|--------|-------------|
| `hf_trending` | HuggingFace | Trending papers, models, datasets |
| `benchmarks` | Papers With Code | SOTA tables, code repos |
| `model_benchmarks` | Epoch AI | Model capabilities comparison |
| `venue_lookup` | DBLP | Conference/journal search |
| `patent_search` | Lens.org | Patent cross-reference |
| `export` | Local | BibTeX/markdown/JSON/CSV export |

### Practitioner Sources (5)
| Tool | Source | Auth Required |
|------|--------|--------------|
| `hn` | Hacker News (Algolia + Firebase) | None |
| `community` | Dev.to + Lobsters | None |
| `packages` | npm + PyPI + crates.io | None |
| `github` | GitHub REST API | Optional `GITHUB_TOKEN` |
| `reddit` | Reddit API | Optional `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` |

### CTO Intelligence (4)
| Tool | What it answers |
|------|----------------|
| `tech_pulse` | "What's trending in AI/dev this week?" (HN + GitHub + Dev.to + HF) |
| `evaluate` | "Should we use X or Y?" (GitHub + Reddit + HN + packages) |
| `sentiment` | "What do devs think about X?" (Reddit + HN) |
| `deep_research` | "Everything about X" (arXiv + GitHub + HN + Reddit + Dev.to + npm) |

### Meta (1)
| Tool | Description |
|------|-------------|
| `help` | Semantic tool discovery — recommends tools for natural language queries |

## Architecture

```
research-mcp-server/
├── src/research_mcp_server/
│   ├── server.py                        # MCP server, tool registration, backwards-compat aliases
│   ├── config.py                        # Configuration (storage path, env vars)
│   │
│   ├── tools/                           # Tool implementations (31 tools)
│   │   ├── search.py                    # Unified search (keyword + structured)
│   │   ├── semantic_search.py           # Embedding-based search
│   │   ├── multi_search.py             # Cross-source search
│   │   ├── download.py                  # Paper download
│   │   ├── list_papers.py              # List papers
│   │   ├── read_paper.py               # Read paper
│   │   ├── read_paper_chunks.py        # Read by section
│   │   ├── citations.py                # Unified citations + analysis
│   │   ├── research_lineage.py         # Influence DAG
│   │   ├── compare.py                  # Paper comparison
│   │   ├── trends.py                   # Trend analysis
│   │   ├── digest.py                   # Research digests
│   │   ├── kb.py                       # Unified KB (save/search/list/annotate/remove)
│   │   ├── kg_query.py                 # Knowledge graph
│   │   ├── memory.py                   # Unified memory (session + persistent)
│   │   ├── export.py                   # Export formats
│   │   ├── hf_papers.py               # HuggingFace trending
│   │   ├── paper_with_code.py         # Papers With Code
│   │   ├── model_benchmarks.py        # Epoch AI
│   │   ├── venue_lookup.py            # DBLP
│   │   ├── patent_search.py           # Lens.org
│   │   ├── hn_tools.py                # Hacker News
│   │   ├── community_tools.py         # Dev.to + Lobsters
│   │   ├── package_tools.py           # npm/PyPI/crates.io
│   │   ├── github_tools.py            # GitHub
│   │   ├── reddit_tools.py            # Reddit
│   │   ├── intelligence_tools.py      # Composite: tech_pulse, evaluate, sentiment, deep_research
│   │   ├── suggest_tools.py           # Semantic tool discovery
│   │   └── (backwards-compat files)   # advanced_query.py, kb_*.py, research_context.py, etc.
│   │
│   ├── clients/                        # External API clients (16 sources)
│   │   ├── arxiv_client.py            # arXiv structured queries
│   │   ├── s2_client.py               # Semantic Scholar
│   │   ├── openalex_client.py         # OpenAlex
│   │   ├── crossref_client.py         # Crossref
│   │   ├── hf_client.py               # HuggingFace Hub
│   │   ├── pwc_client.py              # Papers With Code
│   │   ├── epoch_client.py            # Epoch AI
│   │   ├── dblp_client.py             # DBLP
│   │   ├── lens_client.py             # Lens.org
│   │   ├── hn_client.py               # Hacker News (Algolia + Firebase)
│   │   ├── devto_client.py            # Dev.to
│   │   ├── lobsters_client.py         # Lobsters
│   │   ├── package_client.py          # npm + PyPI + crates.io
│   │   ├── github_client.py           # GitHub REST API
│   │   └── reddit_client.py           # Reddit (OAuth2 + public JSON fallback)
│   │
│   ├── store/                          # Local persistence
│   │   ├── sqlite_store.py            # Paper metadata cache + embeddings
│   │   ├── knowledge_base.py          # KB storage
│   │   ├── knowledge_graph.py         # KG storage
│   │   ├── research_context.py        # Session tracking
│   │   ├── research_memory.py         # Persistent memory (Engram pattern)
│   │   └── research_history.py        # Audit trail
│   │
│   ├── utils/
│   │   ├── formatters.py              # Markdown/JSON formatting
│   │   └── rate_limiter.py            # Token bucket limiters (13 sources)
│   │
│   ├── prompts/                        # MCP prompt templates
│   └── security.py                     # Response sanitization
│
├── tests/                              # 99 tests
├── research/                           # Research docs and specs
├── CLAUDE.md                           # This file
├── pyproject.toml
└── README.md
```

## Setup

### Prerequisites
- Python 3.11+
- uv (package manager)

### Development Setup
```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[test]"
```

### Running the Server
```bash
python -m research_mcp_server
# or
research-mcp-server
# or (backwards compat alias)
arxiv-mcp-server
```

### Testing
```bash
python -m pytest                              # all tests with coverage
python -m pytest tests/test_search.py         # specific test
python -m pytest -v --no-header               # verbose, clean output
```

## Environment Variables

```env
# === No auth needed (core functionality) ===
# arXiv, HN, Dev.to, Lobsters, npm, PyPI, crates.io — all free

# === Recommended (free, unlocks more) ===
GITHUB_TOKEN=ghp_...                    # GitHub PAT — 5000 req/hr (vs 60 without)
REDDIT_CLIENT_ID=...                    # reddit.com/prefs/apps — free
REDDIT_CLIENT_SECRET=...                # reddit.com/prefs/apps — free

# === Optional (enhanced) ===
SEMANTIC_SCHOLAR_API_KEY=...            # Higher S2 rate limits
OPENALEX_EMAIL=...                      # Polite pool (100 req/s)
CROSSREF_EMAIL=...                      # Polite pool
LENS_API_TOKEN=...                      # Patent search
HF_TOKEN=...                            # HuggingFace higher limits
```

## Claude Code MCP Configuration
```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uv",
      "args": [
        "--directory", "/Users/naman/Code/personal/arxiv-mcp-server",
        "run", "research-mcp-server",
        "--storage-path", "/Users/naman/.arxiv-papers"
      ],
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": "",
        "GITHUB_TOKEN": "",
        "REDDIT_CLIENT_ID": "",
        "REDDIT_CLIENT_SECRET": ""
      }
    }
  }
}
```

## Tool Registration Pattern

Tools use `types.Tool` objects + action-based dispatch for consolidated tools:

```python
# Simple tool (one function):
my_tool = types.Tool(name="my_tool", description="...", inputSchema={...})
async def handle_my_tool(arguments: Dict[str, Any]) -> List[types.TextContent]: ...

# Consolidated tool (action dispatch):
kb_tool = types.Tool(name="kb", inputSchema={"properties": {"action": {"enum": [...]}, ...}})
async def handle_kb(arguments):
    if arguments["action"] == "save": return await handle_kb_save(arguments)
    elif arguments["action"] == "search": return await handle_kb_search(arguments)
    ...

# Register in tools/__init__.py: export tool + handler
# Register in server.py: add to list_tools() and _TOOL_HANDLERS dict
```

### Backwards Compatibility
Old tool names (e.g., `search_papers`, `arxiv_advanced_query`, `kb_save`) are registered as aliases in `server.py._TOOL_HANDLERS` and still work. They route to the new consolidated handlers.

## Constraints
- **Async everywhere**: All tools must be `async def`. Use `httpx.AsyncClient`, `aiosqlite`.
- **Graceful degradation**: If any source is down, return partial data with a note. Never crash.
- **Actionable errors**: Suggest fixes (broaden query, check ID format, set env var).
- **Rate limiting**: Every external API call must go through its rate limiter.
- **Response size**: Max 500KB per response (auto-truncated by server.py).

## Code Style
- black formatted, Google-style docstrings
- Type hints on all function signatures
- `json.dumps(result, indent=2)` for JSON tool responses
- Normalize data in client layer, not tool layer

## Common Mistakes
- Forgetting to add new tools to BOTH `list_tools()` AND `_TOOL_HANDLERS` in server.py
- Forgetting to export new tools in `tools/__init__.py`
- Not respecting rate limits (every API call needs `await limiter.wait()`)
- Using S2 paper ID without `ArXiv:` prefix
- Not stripping version suffix (e.g., `v2`) from arXiv IDs before S2 lookup
- Not testing with `python -c "from research_mcp_server.server import server"` after changes
- Stale `tool_index.pkl` after renaming tools — delete from `~/.arxiv-papers/`
