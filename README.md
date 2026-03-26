# research-mcp-server

> Enhanced fork of [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) — a personal research OS for Claude.

An MCP server that turns Claude into a research assistant with persistent memory. Search arXiv, build a personal knowledge base, track citations, analyze trends, and never lose a research finding again.

## What It Does

**21 MCP tools** organized in 4 layers:

| Layer | Tools | Purpose |
|-------|-------|---------|
| **Core** | `search_papers`, `download_paper`, `list_papers`, `read_paper` | Upstream arXiv search and PDF reading |
| **Intelligence** | `arxiv_advanced_query`, `arxiv_semantic_search`, `arxiv_compare_papers`, `arxiv_citation_graph`, `arxiv_citation_context`, `arxiv_research_lineage`, `arxiv_trend_analysis`, `arxiv_research_digest`, `arxiv_export`, `read_paper_chunks` | Semantic search, citation analysis, trend tracking, structured digests with gap analysis |
| **Knowledge Base** | `kb_save`, `kb_search`, `kb_list`, `kb_annotate`, `kb_remove` | Persistent paper storage with tags, collections, notes, reading status, and local vector search |
| **Meta** | `kg_query`, `research_context` | Knowledge graph traversal, research session tracking |

**Plus a web UI** at `/web` for browsing your knowledge base in a browser.

## Quick Start

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Claude Code or Claude Desktop

### Install

```bash
git clone https://github.com/NamanAg0502/arxiv-mcp-server.git
cd arxiv-mcp-server
uv venv && source .venv/bin/activate
uv pip install -e ".[test]"
```

### Configure Claude Code

Add to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "arxiv": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory", "/path/to/arxiv-mcp-server",
        "run", "research-mcp-server"
      ],
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": ""
      }
    }
  }
}
```

### Configure Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/arxiv-mcp-server",
        "run", "research-mcp-server"
      ]
    }
  }
}
```

### Run the Web UI

```bash
cd web
bun install
bun dev
# Open http://localhost:3000
```

## Example Queries

Once configured, ask Claude:

```
# Discovery
Search arXiv for papers on "retrieval augmented generation" from the last 3 months
Find papers by Ashish Vaswani on attention mechanisms

# Deep Analysis
Show me the citation context for paper 1706.03762
Trace the research lineage of BERT — what influenced it and what it spawned
Compare papers 1706.03762, 1810.04805, and 2005.14165

# Knowledge Base
Save paper 2401.12345 to my KB with tags "RAG" and "retrieval"
What's in my knowledge base about transformers?
List my unread papers in the "foundational-papers" collection

# Research Sessions
Start a research session called "MSME Fintech Landscape"
Add open question: "How does OCEN handle credit risk scoring?"
Summarize my current research session

# Digests & Trends
Generate a research digest on "LLM agents" from the last 2 weeks
What's trending in retrieval augmented generation this year?

# Knowledge Graph
What methods appear in my saved papers?
Query my knowledge graph for papers using attention on NER datasets

# Export
Export my foundational-papers collection as BibTeX
```

## Architecture

```
research-mcp-server/
├── src/research_mcp_server/
│   ├── server.py              # MCP server with auto-logging
│   ├── tools/                 # 21 tool implementations
│   ├── clients/               # arXiv + Semantic Scholar API clients
│   ├── store/                 # SQLite stores (KB, KG, history, sessions)
│   └── utils/                 # Rate limiters, formatters
├── web/                       # Next.js web UI
│   ├── src/app/               # Pages: dashboard, papers, search, history
│   └── src/lib/db.ts          # Direct SQLite access (same DB as MCP)
└── tests/                     # 71 tests, 72% coverage
```

### Data Storage

Everything is local SQLite at `~/.arxiv-mcp-server/papers/`:

| File | Purpose |
|------|---------|
| `knowledge_base.db` | Papers, collections, tags, notes, embeddings |
| `knowledge_graph.db` | Papers, concepts, methods, datasets as graph nodes |
| `research_history.db` | Auto-logged tool calls (every query + full response) |
| `research_context.db` | Research sessions, questions, findings |
| `arxiv_cache.db` | Paper metadata cache, embedding cache, digests |

### Key Design Decisions

- **Embeddings**: BAAI/bge-small-en-v1.5 (384-dim, CPU-friendly, 33MB)
- **Hybrid search**: Reciprocal Rank Fusion combining keyword + semantic
- **Auto-logging**: Every MCP tool call persisted with full response
- **Knowledge graph**: Auto-extracted concepts/methods/datasets from papers
- **Graceful degradation**: S2 rate limits don't break the server

## External APIs

| API | Auth | Rate Limit | Used For |
|-----|------|-----------|----------|
| arXiv | None | 1 req/3s | Paper search, metadata, PDFs |
| Semantic Scholar | Free key optional | 1000/s shared | Citations, references, batch lookup |

Get a free S2 API key at [semanticscholar.org](https://www.semanticscholar.org/product/api) for reliable citation tools.

## Development

```bash
# Run tests
python -m pytest tests/ -v

# Format
black src/ tests/

# Run MCP server locally
uv run research-mcp-server --storage-path ~/.arxiv-papers
```

## Web UI Stack

- Next.js 16 (App Router)
- Tailwind CSS 4 + shadcn/ui
- better-sqlite3 (reads same SQLite as MCP server)
- @remixicon/react for icons
- Server components + server actions

## Credits

Enhanced fork of [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) (Apache-2.0).

Intelligence layer, knowledge base, knowledge graph, web UI, and research tooling by [NamanAg0502](https://github.com/NamanAg0502) with Claude Code.
