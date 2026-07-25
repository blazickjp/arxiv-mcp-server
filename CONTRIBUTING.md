# Contributing

Thanks for helping improve arxiv-mcp-server.

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- Open an issue before substantial changes so the scope and interface can be agreed first.
- Keep pull requests focused. Separate unrelated refactors, dependency changes, and feature work.
- Never include downloaded papers, credentials, local indexes, or other private data in a report or commit.

## Development setup

The project requires Python 3.11 or newer and uses [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/blazickjp/arxiv-mcp-server.git
cd arxiv-mcp-server
uv sync --extra test --extra dev
```

Run the local checkout as an MCP server with:

```bash
uv run arxiv-mcp-server --storage-path /tmp/arxiv-mcp-papers
```

The process waits for MCP messages on stdio; it does not print an interactive prompt.

## Tests and formatting

Run these checks before submitting a pull request:

```bash
uv run black --check .
uv run pytest
```

To apply formatting locally:

```bash
uv run black .
```

New behavior should include tests. Bug fixes should include a regression test that fails before the fix and passes afterward.

## Pull requests

A useful pull request includes:

- a short explanation of the problem and chosen approach
- links to related issues
- tests for changed behavior
- documentation updates for user-visible changes
- the exact validation commands run locally

Avoid changing MCP tool names or schemas without discussing compatibility first. Tool output can enter a model's context, so preserve response bounds and treat all remote paper content as untrusted.

## Reporting bugs

Open a [GitHub issue](https://github.com/blazickjp/arxiv-mcp-server/issues) with:

- the package version and installation method
- operating system and Python version
- MCP client and transport (`stdio` or Streamable HTTP)
- minimal reproduction steps
- logs with credentials and private paths removed

Do not report security vulnerabilities in public issues. Follow [SECURITY.md](SECURITY.md) instead.
