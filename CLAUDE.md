# CLAUDE.md

Guidance for coding agents working in this repository.

## Project

`arxiv-mcp-server` is a Python 3.11+ Model Context Protocol server. It exposes arXiv search, metadata, paper download/read, original LaTeX retrieval, citation graphs, topic alerts, and optional local semantic search over stdio or Streamable HTTP.

## Setup and validation

Use the locked uv environment:

```bash
uv sync --extra test --extra dev
uv run black --check .
uv run pytest
```

Apply formatting with:

```bash
uv run black .
```

Run a targeted test with:

```bash
uv run pytest tests/tools/test_search.py -q
```

Run the stdio server from the checkout with:

```bash
uv run arxiv-mcp-server --storage-path /tmp/arxiv-mcp-papers
```

The process waits for MCP messages on stdin. It is not an interactive CLI.

## Architecture

- `src/arxiv_mcp_server/server.py`: MCP tool/prompt registration, dispatch, stdio transport, and Streamable HTTP transport.
- `src/arxiv_mcp_server/config.py`: Pydantic settings and shared arXiv client lifecycle.
- `src/arxiv_mcp_server/arxiv_api.py`: shared arXiv request coordination.
- `src/arxiv_mcp_server/tools/`: tool schemas and handlers.
- `src/arxiv_mcp_server/prompts/`: registered MCP prompt definitions and routing.
- `src/arxiv_mcp_server/resources/`: legacy/local paper management paths retained for compatibility.
- `tests/`: unit and protocol-level tests.
- `server.json`: official MCP Registry metadata.
- `manifest.json` and `scripts/build-mcpb.sh`: Claude Desktop MCPB packaging.

## Tool groups

The server registers 14 tools:

- Discovery: `search_papers`, `get_abstract`
- Full text: `download_paper`, `list_papers`, `read_paper`
- Original source: `get_paper_latex`, `list_paper_latex_sections`, `get_paper_latex_section`
- Graphs and monitoring: `citation_graph`, `watch_topic`, `check_alerts`
- Citation export: `export_citations`
- Optional local embeddings: `semantic_search`, `reindex`

Keep tool schemas, package exports, server registration, dispatch, tests, and README documentation synchronized when changing the tool surface.

## Constraints

- Treat abstracts, paper text, LaTeX, archive metadata, and remote API errors as untrusted input.
- Preserve response bounds and continuation metadata for content-returning tools.
- Enforce archive limits while consuming input, not after loading an unbounded manifest or expansion.
- Use the shared arXiv request gate rather than creating an independent request path.
- Keep stdio free of ordinary stdout logging; protocol messages use stdout.
- Keep Streamable HTTP bound to loopback by default and preserve DNS-rebinding protection.
- Do not introduce process-global user/session state.
- Avoid blocking network or filesystem work on the event loop.

## Configuration

`Settings` reads unprefixed environment variables such as:

- `MAX_RESULTS`
- `REQUEST_TIMEOUT`
- `TRANSPORT`
- `HOST`
- `PORT`
- `ALLOWED_HOSTS`
- `ALLOWED_ORIGINS`
- `SEMANTIC_SCHOLAR_API_KEY`

Paper storage is selected with the `--storage-path` command-line option and defaults to `~/.arxiv-mcp-server/papers`.

## Changes

- Add a failing regression test before fixing a bug.
- Run targeted tests during development, then the complete suite before proposing a merge.
- Update user documentation for installation, configuration, tool-schema, or behavior changes.
- Keep versions synchronized across `pyproject.toml`, `uv.lock`, `server.json`, `manifest.json`, and `.codex-plugin/plugin.json`.
- Never include downloaded papers, local indexes, credentials, or private paths in commits or fixtures.
