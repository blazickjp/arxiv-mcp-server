[![PyPI Version](https://img.shields.io/pypi/v/arxiv-mcp-server.svg)](https://pypi.org/project/arxiv-mcp-server/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/arxiv-mcp-server.svg)](https://pypi.org/project/arxiv-mcp-server/)
[![GitHub Stars](https://img.shields.io/github/stars/blazickjp/arxiv-mcp-server?style=flat)](https://github.com/blazickjp/arxiv-mcp-server/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/blazickjp/arxiv-mcp-server?style=flat)](https://github.com/blazickjp/arxiv-mcp-server/forks)
[![Tests](https://github.com/blazickjp/arxiv-mcp-server/actions/workflows/tests.yml/badge.svg)](https://github.com/blazickjp/arxiv-mcp-server/actions/workflows/tests.yml)
[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![smithery badge](https://smithery.ai/badge/arxiv-mcp-server)](https://smithery.ai/server/arxiv-mcp-server)
[![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/Install_in-VS_Code_Insiders-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=arxiv-mcp-server&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22arxiv-mcp-server%22%5D%7D&quality=insiders)

# ArXiv MCP Server

<!-- mcp-name: io.github.blazickjp/arxiv-mcp-server -->

> 🔍 Enable AI assistants to search and access arXiv papers through a simple MCP interface.

The ArXiv MCP Server provides a bridge between AI assistants and arXiv's research repository through the Model Context Protocol (MCP). It allows AI models to search for papers and access their content in a programmatic way.

<div align="center">
  
🤝 **[Contribute](https://github.com/blazickjp/arxiv-mcp-server/blob/main/CONTRIBUTING.md)** • 
📝 **[Report Bug](https://github.com/blazickjp/arxiv-mcp-server/issues)**

<a href="https://www.pulsemcp.com/servers/blazickjp-arxiv-mcp-server"><img src="https://www.pulsemcp.com/badge/top-pick/blazickjp-arxiv-mcp-server" width="400" alt="Pulse MCP Badge"></a>
</div>

## ✨ Core Features

- 🔎 **Paper Search**: Query arXiv papers with filters for date ranges and categories
- 📄 **Paper Access**: Download and read paper content
- 📋 **Paper Listing**: View all downloaded papers
- 🗃️ **Local Storage**: Papers are saved locally for faster access
- 📝 **Prompts**: A set of research prompts for paper analysis



## 🚀 Quick Start

### Installing via Smithery

To install ArXiv Server for Claude Desktop automatically via [Smithery](https://smithery.ai/server/arxiv-mcp-server):

```bash
npx -y @smithery/cli install arxiv-mcp-server --client claude
```

### Installing Manually

> **Important — use `uv tool install`, not `uv pip install`**
>
> Running `uv pip install arxiv-mcp-server` installs the package into the
> current virtual environment but does **not** place the `arxiv-mcp-server`
> executable on your `PATH`.  You must use `uv tool install` so that uv
> creates an isolated environment and exposes the executable globally:

```bash
uv tool install arxiv-mcp-server
```

After this, the `arxiv-mcp-server` command will be available on your `PATH`.

> **PDF fallback (older papers):** Most arXiv papers have an HTML version which
> the base install handles automatically. For older papers that only have a PDF,
> the server needs the `[pdf]` extra (pymupdf4llm). Install it with:
>
> ```bash
> uv tool install 'arxiv-mcp-server[pdf]'
> ```
You can verify it with:

```bash
arxiv-mcp-server --help
```

If you previously ran `uv pip install arxiv-mcp-server` and the command is
missing, uninstall it and re-install with `uv tool install` as shown above.

For development:

```bash
# Clone and set up development environment
git clone https://github.com/blazickjp/arxiv-mcp-server.git
cd arxiv-mcp-server

# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install with test dependencies (development only — no global executable)
uv pip install -e ".[test]"
```

### 🔌 MCP Integration

Add this configuration to your MCP client config file:

```json
{
    "mcpServers": {
        "arxiv-mcp-server": {
            "command": "uv",
            "args": [
                "tool",
                "run",
                "arxiv-mcp-server",
                "--storage-path", "/path/to/paper/storage"
            ]
        }
    }
}
```

For Development:

```json
{
    "mcpServers": {
        "arxiv-mcp-server": {
            "command": "uv",
            "args": [
                "--directory",
                "path/to/cloned/arxiv-mcp-server",
                "run",
                "arxiv-mcp-server",
                "--storage-path", "/path/to/paper/storage"
            ]
        }
    }
}
```

## 🔒 Security Note

arXiv papers are user-generated, untrusted content. Paper text returned by this
server may contain prompt injection attempts — crafted text designed to manipulate
an AI assistant's behavior. Treat all paper content as untrusted input.

In production environments, apply appropriate sandboxing and avoid feeding raw
paper content into agentic pipelines that have access to sensitive tools or data
without review. See [SECURITY.md](SECURITY.md) for the full security policy.

## 💡 Available Tools

### Core Workflow

The typical workflow for deep paper research is:

```
search_papers → download_paper → read_paper
```

`list_papers` shows what you have locally. `semantic_search` searches across your local collection.

---

### 1. Paper Search
Search arXiv with optional category, date, and boolean filters. Enforces arXiv's 3-second rate limit automatically. If rate limited, wait 60 seconds before retrying.

```python
result = await call_tool("search_papers", {
    "query": "\"KAN\" OR \"Kolmogorov-Arnold Networks\"",
    "max_results": 10,
    "date_from": "2024-01-01",
    "categories": ["cs.LG", "cs.AI"],
    "sort_by": "date"   # or "relevance" (default)
})
```

Supported categories include `cs.AI`, `cs.LG`, `cs.CL`, `cs.CV`, `cs.NE`, `stat.ML`, `math.OC`, `quant-ph`, `eess.SP`, and more. See tool description for the full list.

### 2. Paper Download
Download a paper by its arXiv ID. Tries HTML first, falls back to PDF. Stores the paper locally for `read_paper` and `semantic_search`.

```python
result = await call_tool("download_paper", {
    "paper_id": "2401.12345"
})
```

> For older papers that only have a PDF, install the `[pdf]` extra: `uv tool install 'arxiv-mcp-server[pdf]'`

### 3. List Papers
List all papers downloaded locally. Returns arXiv IDs only — use `read_paper` to access content.

```python
result = await call_tool("list_papers", {})
```

### 4. Read Paper
Read the full text of a locally downloaded paper in markdown. **Requires `download_paper` to be called first.**

```python
result = await call_tool("read_paper", {
    "paper_id": "2401.12345"
})
```



## 📝 Research Prompts

The server offers specialized prompts to help analyze academic papers:

### Paper Analysis Prompt
A comprehensive workflow for analyzing academic papers that only requires a paper ID:

```python
result = await call_prompt("deep-paper-analysis", {
    "paper_id": "2401.12345"
})
```

This prompt includes:
- Detailed instructions for using available tools (list_papers, download_paper, read_paper, search_papers)
- A systematic workflow for paper analysis
- Comprehensive analysis structure covering:
  - Executive summary
  - Research context
  - Methodology analysis
  - Results evaluation
  - Practical and theoretical implications
- Future research directions
- Broader impacts

### Pro Prompt Pack

- `summarize_paper`: concise structured summary for one paper.
- `compare_papers`: side-by-side technical comparison across paper IDs.
- `literature_review`: thematic synthesis across a topic and optional paper set.

## ⚙️ Configuration

Configure through environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `ARXIV_STORAGE_PATH` | Paper storage location | ~/.arxiv-mcp-server/papers |

## 🧪 Testing

Run the test suite:

```bash
python -m pytest
```

## 🧪 Experimental Features

> **These features are not yet fully tested and may behave unexpectedly. Use with caution.**

The following tools require additional dependencies and are under active development:

```bash
uv pip install -e ".[pro]"
```

### Semantic Search
Semantic similarity search over your **locally downloaded** papers only. Returns empty results if no papers have been downloaded yet. Requires `[pro]` dependencies.

```python
result = await call_tool("semantic_search", {
    "query": "test-time adaptation in multimodal transformers",
    "max_results": 5
})
# or find papers similar to a known paper:
result = await call_tool("semantic_search", {
    "paper_id": "2404.19756",
    "max_results": 5
})
```

### Citation Graph
Fetch references and citing papers via Semantic Scholar. Works on any arXiv ID — no local download required.

```python
result = await call_tool("citation_graph", {
    "paper_id": "2401.12345"
})
```

### Research Alerts
Save topic watches and poll for newly published papers since the last check. Uses the same query syntax as `search_papers`.

```python
# Register a watch (idempotent — calling again updates the existing watch)
await call_tool("watch_topic", {
    "topic": "\"multi-agent reinforcement learning\"",
    "categories": ["cs.AI", "cs.LG"],
    "max_results": 10
})

# Check all watches — returns only papers published since last check
result = await call_tool("check_alerts", {})

# Check a single watch
result = await call_tool("check_alerts", {"topic": "\"multi-agent reinforcement learning\""})
```

### Advanced Prompts
`summarize_paper`, `compare_papers`, and `literature_review` for deeper research workflows. Requires `[pro]` dependencies.

---

## 📄 License

Released under the MIT License. See the LICENSE file for details.

---

<div align="center">

Made with ❤️ by the Pearl Labs Team

<a href="https://glama.ai/mcp/servers/04dtxi5i5n"><img width="380" height="200" src="https://glama.ai/mcp/servers/04dtxi5i5n/badge" alt="ArXiv Server MCP server" /></a>
</div>
