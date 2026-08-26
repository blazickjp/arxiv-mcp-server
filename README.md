# arxiv-mcp-server

<!-- mcp-name: io.github.blazickjp/arxiv-mcp-server -->

[![PyPI](https://img.shields.io/pypi/v/arxiv-mcp-server.svg)](https://pypi.org/project/arxiv-mcp-server/)
[![Downloads](https://static.pepy.tech/badge/arxiv-mcp-server)](https://pypi.org/project/arxiv-mcp-server/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-listed,_latest_0.7.2-5C5CFF?style=flat-square)](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.blazickjp%2Farxiv-mcp-server/versions/latest)

[![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/Install_in-VS_Code_Insiders-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D&quality=insiders)
[![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=arxiv-mcp-server&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Install-D97757?style=flat-square&logo=anthropic&logoColor=white)](#claude-code)
[![OpenAI Codex](https://img.shields.io/badge/OpenAI_Codex-Install-000000?style=flat-square&logo=openai&logoColor=white)](#openai-codex)
[![Hermes Agent](https://img.shields.io/badge/Hermes_Agent-Install-6C5CE7?style=flat-square)](#hermes-agent)

A local MCP server for agent literature work. The differentiator is original-LaTeX section reads, BibTeX from arXiv metadata, and topic watches. Papers stay on disk. The working loop is paper ID → outline → one section → citations. Search is optional.

## Install

The default install is `uvx arxiv-mcp-server`. Command-based integrations need [uv](https://docs.astral.sh/uv/getting-started/installation/), which provides `uvx`. No repository clone or Python environment setup is required.

```bash
uvx arxiv-mcp-server
```

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

The supported package is published on PyPI as `arxiv-mcp-server==0.7.2`. An unrelated npm package uses the same name, so do not install this server with npm, pnpm, or `npx arxiv-mcp-server`.

Listed on the [official MCP registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.blazickjp%2Farxiv-mcp-server/versions/latest), latest 0.7.2.

## Why this is not a search wrapper

Search, source retrieval, citation graphs, and downloads call their respective external services. What stays local is the literature loop: read author-submitted LaTeX one section at a time, export BibTeX from authoritative arXiv metadata, and keep topic watches on disk. The server runs locally over stdio by default.

<details>
<summary>Per-client recipes (Claude Code, Codex, Hermes, VS Code / Kiro, Claude Desktop, plugins)</summary>

Use the default JSON above unless your client has a one-line helper.

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

[![Hermes Agent](https://img.shields.io/badge/Hermes_Agent-Install-6C5CE7?style=flat-square)](#hermes-agent)

Add the server, approve the discovered tools, and test the saved connection:

```bash
hermes mcp add arxiv --command uvx --args arxiv-mcp-server
hermes mcp test arxiv
```

### VS Code and Kiro

[![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/Install_in-VS_Code_Insiders-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D&quality=insiders)
[![Add to Kiro](https://kiro.dev/images/add-to-kiro.svg)](https://kiro.dev/launch/mcp/add?name=arxiv-mcp-server&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%7D)

For the richer Kiro Power integration, open the **Powers** panel, choose **Add Custom Power → Import power from GitHub**, and enter:

```text
https://github.com/blazickjp/arxiv-mcp-server
```

The Power installs the MCP connection from `mcp.json` and adds focused arXiv research guidance. Kiro users who prefer manual configuration can place the generic configuration above in `.kiro/settings/mcp.json` for one workspace or `~/.kiro/settings/mcp.json` for all workspaces.

### Claude Desktop bundle

macOS users can install a bundled `.mcpb` extension from the [v0.7.2 release](https://github.com/blazickjp/arxiv-mcp-server/releases/tag/v0.7.2) or the [latest GitHub release](https://github.com/blazickjp/arxiv-mcp-server/releases/latest):

- Apple Silicon: [`arxiv-mcp-server-darwin-arm64-0.7.2.mcpb`](https://github.com/blazickjp/arxiv-mcp-server/releases/download/v0.7.2/arxiv-mcp-server-darwin-arm64-0.7.2.mcpb)
- Intel: [`arxiv-mcp-server-darwin-x86_64-0.7.2.mcpb`](https://github.com/blazickjp/arxiv-mcp-server/releases/download/v0.7.2/arxiv-mcp-server-darwin-x86_64-0.7.2.mcpb)

Double-click the bundle, drag it into Claude Desktop, or open **Settings → Extensions → Advanced settings → Install Extension…**. The bundle includes the server dependencies and requires CPython 3.11.x.

### Other MCP clients

Other clients may use a top-level `servers` object, TOML, or their own settings UI; consult the client MCP documentation. Direct MCP installation is the shortest path. Install a plugin when you also want the research workflow that steers the client toward focused searches, bounded reads, citation traversal, and section-level LaTeX retrieval.

### Plugin manifests

The same MCP server and research skill are packaged for both major plugin systems:

| Integration | Manifest | Marketplace |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` | `.claude-plugin/marketplace.json` |
| OpenAI Codex / ChatGPT Work | `.codex-plugin/plugin.json` | `.agents/plugins/marketplace.json` |
| Kiro Power | `POWER.md` | `mcp.json` |
| Shared MCP launch | `.mcp.json` for Claude and repository-local clients; `.codex-mcp.json` for Codex plugins | `uvx arxiv-mcp-server` |
| Shared research workflow | `skills/arxiv-mcp-server/SKILL.md` | Installed with either plugin |

</details>

## If a desktop client cannot find `uvx`

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

## If an existing installation is missing newer tools

`uvx` reuses cached tool environments. Force it to resolve the current PyPI release with a supported interpreter, then restart your MCP client:

```bash
uvx --python 3.11 --refresh-package arxiv-mcp-server arxiv-mcp-server
```

If your client still launches an older environment, add `"--python", "3.11"` before `"arxiv-mcp-server"` in its `args` array.

## Persistent command install

To place `arxiv-mcp-server` on your `PATH` instead of launching it through `uvx`:

```bash
uv tool install arxiv-mcp-server
```

If the command is not immediately available, run `uv tool update-shell` and restart the terminal. Afterward, use `"command": "arxiv-mcp-server"` and omit the package name from `args`.

## Tools

The server currently exposes 19 tools.

| Tool | Purpose | Notes |
|---|---|---|
| `search_papers` | Search arXiv by query, category, date, and sort order | Default ≤5 compact results (`abstract_mode=snippet`); remote arXiv API |
| `get_abstract` | Fetch metadata and an abstract by arXiv ID | Does not download the paper |
| `download_paper` | Download and convert a paper to local Markdown | HTML first; PDF fallback uses `[pdf]`; `force=true` re-fetches; content bounded to 12,000 chars by default |
| `list_papers` | List papers stored locally | Returns id, title, authors, published; `compact` for IDs only |
| `read_paper` | Read locally stored paper content | Bounded to 12,000 chars by default; supports `start`/`max_chars`/`return_full_text` |
| `get_paper_outline` | Paginated markdown heading outline | Stable hierarchical section IDs |
| `read_paper_section` | Read one bounded markdown section | By outline ID or unique title |
| `search_paper_text` | Bounded passage search in a paper | Source offsets; no Torch required |
| `get_paper_latex` | Retrieve bounded author-submitted LaTeX | Remote arXiv source archive |
| `list_paper_latex_sections` | Return a paginated LaTeX outline | Supports `start` and `max_sections` |
| `get_paper_latex_section` | Read one bounded LaTeX section | Select by outline ID or exact title |
| `citation_graph` | Fetch references and citing papers | Remote Semantic Scholar API; optional `SEMANTIC_SCHOLAR_API_KEY` |
| `export_citations` | Export BibTeX for one or more arXiv IDs | Authoritative arXiv metadata |
| `watch_topic` | Save or update an arXiv topic watch | Stored locally; omit `categories` to preserve on update, `categories: []` to clear |
| `list_watches` | List saved topic watches | Read-only; does not advance last_checked |
| `check_alerts` | Check saved watches for new papers | Returns papers since the last check |
| `unwatch_topic` | Delete a saved topic watch | Exact topic match; not-found if missing |
| `semantic_search` | Search downloaded papers by semantic similarity | Requires `[pro]` |
| `reindex` | Rebuild the local semantic index | Requires `[pro]` |

### Research alerts (`watch_topic`)

Save standing topic watches with `watch_topic`, inspect them with `list_watches`, poll with `check_alerts`, and remove with `unwatch_topic`.

When updating an existing watch (same `topic` string):

- **Omit** `categories` → **preserve** the stored category filters (and other fields you leave unchanged).
- Pass **`categories: []`** → **clear** category filters.
- Pass a non-empty list → replace the stored filters.

Create path: omitting `categories` stores an empty list (no category filter).

### search_papers query guide

Tool schemas stay short on purpose. Use this section (not the always-loaded MCP description) for query tutorials, category catalogs, and workflow examples.

**Query construction**

- Use quoted phrases for exact matches: `"multi-agent systems"`, `"neural networks"`
- Combine related concepts with OR: `"AI agents" OR "software agents"`
- Field-specific searches: `ti:"exact title phrase"`, `au:"author name"`, `abs:"keyword"`, `cat:cs.LG`
- Exclude with ANDNOT: `"machine learning" ANDNOT "survey"`
- Prefer 2–4 core concepts over long keyword lists

**Advanced patterns**

- Field + phrase: `ti:"transformer architecture"`
- Multiple fields: `au:"Smith" AND ti:"quantum"`
- Exclusions: `"deep learning" ANDNOT ("survey" OR "review")`
- Broad + narrow: `"artificial intelligence" AND (robotics OR "computer vision")`

**Category filtering** (recommended for relevance)

Computer Science: `cs.AI` (AI), `cs.LG` (ML), `cs.CL` (NLP), `cs.CV` (vision), `cs.MA` (multi-agent), `cs.RO` (robotics), `cs.NE` (neural/evolutionary), `cs.IR` (IR), `cs.HC` (HCI), `cs.CR` (security), `cs.DB` (databases)

Statistics & Math: `stat.ML`, `stat.AP`, `math.OC`, `math.ST`

Physics & other: `quant-ph`, `eess.SP`, `eess.AS`, `physics.data-an`

**Effective examples**

- `ti:"reinforcement learning"` with `categories: ["cs.LG", "cs.AI"]`
- `au:"Hinton" AND "deep learning"` with `categories: ["cs.LG"]`
- `"multi-agent" ANDNOT "survey"` with `categories: ["cs.MA"]`
- `abs:"transformer" AND ti:"attention"` with `categories: ["cs.CL"]`

**Dates and sorting**

- Dates use `YYYY-MM-DD` (`date_from` / `date_to`)
- Default `sort_by` is `relevance`; use `date` for newest-first monitoring
- Foundational work: `date_to: "2010-12-31"` with title/abstract field searches

**Result size, abstracts, and pagination**

- Default `max_results` is **5** (cap 50). Pass an explicit value for larger pages.
- `abstract_mode`: `snippet` (default, ~280 chars, marked `… [truncated]` when cut), `full` (complete abstract), or `none` (omit abstracts). Other metadata (title, authors, categories, dates, URLs) is always returned.
- Responses report `total_results` (corpus hits), `returned`, `has_more`, `start`, `next_start`, and `abstract_mode`
- Pass `start=next_start` with the same `abstract_mode` for the next page
- arXiv enforces ~3 seconds between requests (handled server-side); on rate-limit errors wait ~60s

### Search and inspect a paper

Ask your MCP client to call `search_papers` with:

```json
{
  "query": "\"Kolmogorov-Arnold Networks\"",
  "categories": ["cs.LG", "cs.AI"],
  "sort_by": "date"
}
```

Defaults return up to five compact results with abstract snippets. Use `"abstract_mode": "full"` when you need complete abstracts in the search response, or call `get_abstract` for a single paper after a compact search:

```json
{
  "paper_id": "2404.19756"
}
```

Do not call `get_abstract` again for papers already returned with `abstract_mode=full`.

### Download and read full text

Call `download_paper` with:

```json
{
  "paper_id": "2404.19756"
}
```

Omitting `max_chars` returns a bounded first chunk (default **12,000** paper characters). Cached papers are returned immediately. Pass `"force": true` to re-download and overwrite the local markdown and sidecar (also happens automatically when the HTML extractor version changes).

Then page through the cached content with `read_paper`:

```json
{
  "paper_id": "2404.19756",
  "start": 0
}
```

Or continue from a prior chunk:

```json
{
  "paper_id": "2404.19756",
  "start": 12000
}
```

Large-content responses include `content_length`, `returned_chars`, `next_start`, `is_truncated`, and (when truncated) `next_retrieval` with the next-call instruction. Pass `next_start` into the next call's `start` to continue reading. Pass an explicit `max_chars` to override the default chunk size, or `"return_full_text": true` to opt into the previous unbounded full-paper response.

#### Migration notes (bounded content default)

Previously, omitting `max_chars` on `download_paper` / `read_paper` returned the **entire** paper. That default is now a **12,000-character** chunk so a single MCP tool call cannot flood the client context window.

| Need | Call |
|---|---|
| First bounded chunk (new default) | `{ "paper_id": "…" }` |
| Continue reading | `{ "paper_id": "…", "start": <next_start> }` |
| Custom chunk size | `{ "paper_id": "…", "max_chars": 5000 }` |
| Old unbounded behavior | `{ "paper_id": "…", "return_full_text": true }` |

Clients that already passed `max_chars` are unchanged. Only callers that relied on the omitted-`max_chars` = full-text behavior need to add `return_full_text: true` or page via `next_start`.

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
| `SEMANTIC_SCHOLAR_API_KEY` | empty | Optional Semantic Scholar API key for `citation_graph` |

Environment variable names are case-insensitive through Pydantic settings. `--storage-path` is a command-line option rather than an environment setting.

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

