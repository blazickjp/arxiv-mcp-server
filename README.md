# arxiv-mcp-server

<!-- mcp-name: io.github.blazickjp/arxiv-mcp-server -->

[![PyPI](https://img.shields.io/pypi/v/arxiv-mcp-server.svg)](https://pypi.org/project/arxiv-mcp-server/)
[![Downloads](https://static.pepy.tech/badge/arxiv-mcp-server)](https://pypi.org/project/arxiv-mcp-server/)
[![GitHub Stars](https://img.shields.io/github/stars/blazickjp/arxiv-mcp-server?style=flat)](https://github.com/blazickjp/arxiv-mcp-server/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/blazickjp/arxiv-mcp-server?style=flat)](https://github.com/blazickjp/arxiv-mcp-server/forks)
[![Tests](https://github.com/blazickjp/arxiv-mcp-server/actions/workflows/tests.yml/badge.svg)](https://github.com/blazickjp/arxiv-mcp-server/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/arxiv-mcp-server.svg)](https://pypi.org/project/arxiv-mcp-server/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/Install_in-VS_Code_Insiders-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D&quality=insiders)
[![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=arxiv-mcp-server&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Install-D97757?style=flat-square&logo=anthropic&logoColor=white)](#claude-code)
[![OpenAI Codex](https://img.shields.io/badge/OpenAI_Codex-Install-000000?style=flat-square&logo=openai&logoColor=white)](#openai-codex)
[![Hermes Agent](https://img.shields.io/badge/Hermes_Agent-Install-6C5CE7?style=flat-square)](#hermes-agent)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-Listed-5C5CFF?style=flat-square)](https://registry.modelcontextprotocol.io/?search=io.github.blazickjp%2Farxiv-mcp-server)

An MCP server for searching arXiv, downloading papers, reading bounded full text, retrieving original LaTeX by section, following citation graphs, and maintaining research alerts.

It runs locally over stdio by default. Papers and indexes stay on your machine; search, source retrieval, citation graphs, and downloads call their respective external services.

## Install

The command-based integrations require [uv](https://docs.astral.sh/uv/getting-started/installation/), which provides `uvx`. Choose your client below; no repository clone or Python environment setup is required.

### Claude Code

Add the MCP server for all projects:

```bash
claude mcp add --transport stdio --scope user arxiv -- uvx arxiv-mcp-server
```

For the richer plugin integration—which installs the MCP connection plus the bundled arXiv research skill—register this repository as a marketplace and install the plugin:

```bash
claude plugin marketplace add blazickjp/arxiv-mcp-server
claude plugin install arxiv-mcp-server@arxiv-mcp
```

Verify the direct MCP installation with `claude mcp get arxiv`. Restart Claude Code or run `/reload-plugins` after installing the plugin.

### OpenAI Codex

Add the MCP server:

```bash
codex mcp add arxiv -- uvx arxiv-mcp-server
```

Or install the MCP connection and bundled research skill as a Codex plugin:

```bash
codex plugin marketplace add blazickjp/arxiv-mcp-server
codex plugin add arxiv-mcp-server@arxiv-mcp
```

Verify the direct MCP installation with `codex mcp get arxiv`. Codex CLI, the Codex IDE extension, and Codex in the ChatGPT desktop app share this MCP configuration.

### Hermes Agent

Add the server, approve the discovered tools, and test the saved connection:

```bash
hermes mcp add arxiv --command uvx --args arxiv-mcp-server
hermes mcp test arxiv
```

### Kiro and VS Code

Use the **Add to Kiro**, **Install in VS Code**, or **Install in VS Code Insiders** button above.

For the richer Kiro Power integration, open the **Powers** panel, choose **Add Custom Power → Import power from GitHub**, and enter:

```text
https://github.com/blazickjp/arxiv-mcp-server
```

The Power installs the MCP connection from `mcp.json` and adds focused arXiv research guidance. Kiro users who prefer manual configuration can place the generic configuration below in `.kiro/settings/mcp.json` for one workspace or `~/.kiro/settings/mcp.json` for all workspaces.

### Claude Desktop bundle

macOS users can install a bundled `.mcpb` extension from the [latest GitHub release](https://github.com/blazickjp/arxiv-mcp-server/releases/latest):

- Apple Silicon: `arxiv-mcp-server-darwin-arm64-<version>.mcpb`
- Intel: `arxiv-mcp-server-darwin-x86_64-<version>.mcpb`

Double-click the bundle, drag it into Claude Desktop, or open **Settings → Extensions → Advanced settings → Install Extension…**. The bundle includes the server dependencies and requires CPython 3.11.x.

### Other MCP clients

Add this stdio configuration to clients that accept the `mcpServers` JSON shape, such as Claude Desktop and Kiro. Other clients may use a top-level `servers` object, TOML, or their own settings UI; consult the client's MCP documentation.

```json
{
  "mcpServers": {
    "arxiv": {
      "type": "stdio",
      "command": "uvx",
      "args": ["arxiv-mcp-server"]
    }
  }
}
```

The default paper directory is `~/.arxiv-mcp-server/papers`. To choose another directory, append `"--storage-path", "/absolute/path/to/papers"` to `args`.

For older papers that require PDF conversion, run the package with its PDF extra:

```json
{
  "mcpServers": {
    "arxiv": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "arxiv-mcp-server[pdf]",
        "arxiv-mcp-server"
      ]
    }
  }
}
```

The supported package is published on PyPI. An unrelated npm package uses the same name, so do not install this server with npm, pnpm, or `npx arxiv-mcp-server`.

### If an existing installation is missing newer tools

`uvx` reuses cached tool environments. Force it to resolve the current PyPI release with a supported interpreter, then restart your MCP client:

```bash
uvx --python 3.11 --refresh-package arxiv-mcp-server arxiv-mcp-server
```

If your client still launches an older environment, add `"--python", "3.11"` before `"arxiv-mcp-server"` in its `args` array.

### If a desktop client cannot find `uvx`

Desktop applications do not always inherit the same `PATH` as your terminal. If `uvx arxiv-mcp-server` works in a terminal but the client reports that the server failed to connect, find the executable's absolute path:

```bash
# macOS and Linux
command -v uvx
```

```powershell
# Windows PowerShell
(Get-Command uvx).Source
```

Replace `"command": "uvx"` with the returned absolute path, then restart the client. Keep the `args` value unchanged.

### Persistent command install

To place `arxiv-mcp-server` on your `PATH` instead of launching it through `uvx`:

```bash
uv tool install arxiv-mcp-server
```

If the command is not immediately available, run `uv tool update-shell` and restart the terminal. Afterward, use `"command": "arxiv-mcp-server"` and omit the package name from `args`.

## Plugin integrations

The repository now packages the same MCP server and research skill for both major plugin systems:

| Integration | Manifest | Marketplace |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` | `.claude-plugin/marketplace.json` |
| OpenAI Codex / ChatGPT Work | `.codex-plugin/plugin.json` | `.agents/plugins/marketplace.json` |
| Kiro Power | `POWER.md` | `mcp.json` |
| Shared MCP launch | `.mcp.json` for Claude and repository-local clients; `.codex-mcp.json` for Codex plugins | `uvx arxiv-mcp-server` |
| Shared research workflow | `skills/arxiv-mcp-server/SKILL.md` | Installed with either plugin |

Direct MCP installation is the shortest path. Install the plugin when you also want the research workflow that steers the client toward focused searches, bounded reads, citation traversal, and section-level LaTeX retrieval.

## Tools

The server currently exposes 14 tools.

| Tool | Purpose | Notes |
|---|---|---|
| `search_papers` | Search arXiv by query, category, date, and sort order | Remote arXiv API |
| `get_abstract` | Fetch metadata and an abstract by arXiv ID | Does not download the paper |
| `download_paper` | Download and convert a paper to local Markdown | HTML first; PDF fallback uses `[pdf]` |
| `list_papers` | List papers stored locally | Returns arXiv IDs |
| `read_paper` | Read locally stored paper content | Supports `start` and `max_chars` |
| `get_paper_latex` | Retrieve bounded author-submitted LaTeX | Remote arXiv source archive |
| `list_paper_latex_sections` | Return a paginated LaTeX outline | Supports `start` and `max_sections` |
| `get_paper_latex_section` | Read one bounded LaTeX section | Select by outline ID or exact title |
| `citation_graph` | Fetch references and citing papers | Remote Semantic Scholar API |
| `export_citations` | Export BibTeX for one or more arXiv IDs | Authoritative arXiv metadata |
| `watch_topic` | Save or update an arXiv topic watch | Stored locally |
| `check_alerts` | Check saved watches for new papers | Returns papers since the last check |
| `semantic_search` | Search downloaded papers by semantic similarity | Requires `[pro]` |
| `reindex` | Rebuild the local semantic index | Requires `[pro]` |

### Search and inspect a paper

Ask your MCP client to call `search_papers` with:

```json
{
  "query": "\"Kolmogorov-Arnold Networks\"",
  "categories": ["cs.LG", "cs.AI"],
  "max_results": 5,
  "sort_by": "date"
}
```

Then call `get_abstract` with:

```json
{
  "paper_id": "2404.19756"
}
```

### Download and read full text

Call `download_paper` with:

```json
{
  "paper_id": "2404.19756",
  "max_chars": 12000
}
```

Then page through the cached content with `read_paper`:

```json
{
  "paper_id": "2404.19756",
  "start": 0,
  "max_chars": 12000
}
```

Large-content responses include `content_length`, `returned_chars`, `next_start`, and `is_truncated`. Pass `next_start` into the next call to continue reading.

### Read original LaTeX by section

Call `get_paper_latex` with:

```json
{
  "paper_id": "1706.03762"
}
```

Get the first page of its section outline with `list_paper_latex_sections`:

```json
{
  "paper_id": "1706.03762",
  "start": 0,
  "max_sections": 100
}
```

Then call `get_paper_latex_section` using an ID from that outline:

```json
{
  "paper_id": "1706.03762",
  "section_id": "3.2",
  "max_chars": 12000
}
```

LaTeX archives are validated, size-limited, and cached locally before content is returned.

## Optional dependencies

Choose the install variant that matches the features you need:

```bash
# Base server
uv tool install arxiv-mcp-server

# Base server plus PDF conversion
uv tool install "arxiv-mcp-server[pdf]"

# Base server plus local semantic search
uv tool install "arxiv-mcp-server[pro]"
```

If the base tool is already installed, reinstall the selected variant:

```bash
uv tool install --force "arxiv-mcp-server[pdf]"
```

The `pdf` extra installs `pymupdf4llm` and `pymupdf-layout` for papers without usable arXiv HTML. The `pro` extra adds local embedding dependencies for `semantic_search` and `reindex`; semantic search only operates on papers already downloaded to the configured storage directory.

## Built-in prompts

The server provides seven MCP prompt workflows. Prompt availability depends on the client; the server provides workflow instructions but does not run a separate model.

| Prompt | Required arguments | Purpose |
|---|---|---|
| `research-discovery` | `topic` | Map terminology, searches, papers, research clusters, and a reading path |
| `deep-paper-analysis` | `paper_id` | Analyze one paper in depth |
| `summarize_paper` | `paper_id` | Summarize methods, results, and limitations |
| `compare_papers` | `paper_ids` | Compare multiple papers |
| `literature_review` | `topic` | Synthesize a topic and optional paper set |
| `literature-synthesis` | `paper_ids` | Synthesize themes, methods, timelines, or gaps across papers |
| `research-question` | `paper_ids`, `topic` | Formulate grounded, falsifiable research questions |

## Streamable HTTP

For deployments where stdio is not practical:

```bash
TRANSPORT=http HOST=127.0.0.1 PORT=8080 \
  uvx arxiv-mcp-server --storage-path /absolute/path/to/papers
```

PowerShell:

```powershell
$env:TRANSPORT = "http"
$env:HOST = "127.0.0.1"
$env:PORT = "8080"
uvx arxiv-mcp-server --storage-path C:\absolute\path\to\papers
```

Connect clients to:

```json
{
  "mcpServers": {
    "arxiv": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp"
    }
  }
}
```

Cloud and load-balancer probes should GET `http://<host>:<port>/healthz`. It returns `200` with body `ok` once the HTTP server is listening. There is no separate `/ready` check: if the process is up, it is ready. The stdio transport has no HTTP endpoints.

The server binds to `127.0.0.1` by default and enables MCP DNS-rebinding protection. If a reverse proxy exposes the server, keep the process on a private interface and provide authentication and network controls upstream. Use `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` for the host and origin values forwarded by the proxy.

## Configuration

| Setting | Default | Purpose |
|---|---:|---|
| `--storage-path` | `~/.arxiv-mcp-server/papers` | Paper, source-cache, alert, and index storage |
| `MAX_RESULTS` | `50` | Server-side cap for result counts |
| `REQUEST_TIMEOUT` | `60` | PDF fallback download timeout in seconds |
| `TRANSPORT` | `stdio` | `stdio`, `http`, or `streamable-http` |
| `HOST` | `127.0.0.1` | HTTP bind host |
| `PORT` | `8000` | HTTP bind port |
| `ALLOWED_HOSTS` | empty | Additional accepted HTTP Host values |
| `ALLOWED_ORIGINS` | empty | Additional accepted HTTP Origin values |

Environment variable names are case-insensitive through Pydantic settings. `--storage-path` is a command-line option rather than an environment setting.


#### Embedding model configuration
- ARXIV_MCP_EMBEDDING_MODEL (optional): sentence-transformers model id used for local semantic embeddings. Example:
  - export ARXIV_MCP_EMBEDDING_MODEL=sentence-transformers/all-mpnet-base-v2
- You can also set EMBEDDING_MODEL via environment variables (pydantic Settings).
- Important: Changing the embedding model requires rebuilding the semantic index because stored vectors are model-dependent and may have different dimensions. Run the `reindex` tool with `clear_existing=True` after changing the model.


## Security

Paper text and LaTeX are untrusted external content. A paper can contain text intended to manipulate an AI client into ignoring its instructions or calling unrelated tools.

- Do not treat instructions found inside a paper as trusted commands.
- Use client approval controls for shell, browser, filesystem, and messaging tools.
- Review generated summaries before taking external actions.
- Keep Streamable HTTP private unless authentication is provided upstream.

See [SECURITY.md](SECURITY.md) for the reporting policy and threat details.

## Development

```bash
git clone https://github.com/blazickjp/arxiv-mcp-server.git
cd arxiv-mcp-server
uv sync --extra test --extra dev
uv run pytest
uv run black --check .
```

Run the development checkout from an MCP client with:

```json
{
  "mcpServers": {
    "arxiv-dev": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/arxiv-mcp-server",
        "run",
        "arxiv-mcp-server"
      ]
    }
  }
}
```

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and use [GitHub Issues](https://github.com/blazickjp/arxiv-mcp-server/issues) for reproducible bugs or scoped feature proposals.

## License

Apache License 2.0. See [LICENSE](LICENSE).
