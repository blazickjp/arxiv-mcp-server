# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Environment Setup
```bash
# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install with test dependencies
uv pip install -e ".[test]"
```

### Testing
```bash
# Run all tests with coverage
python -m pytest

# Run specific test file
python -m pytest tests/tools/test_search.py

# Run tests with verbose output
python -m pytest -v
```

### Running the Server
```bash
# Run as module
python -m arxiv_mcp_server

# Or via entry point
arxiv-mcp-server
```

## Architecture Overview

This is an **MCP (Message Control Protocol) server** that provides AI models access to arXiv research papers. The codebase follows a modular architecture with four main layers:

### Core Components

1. **Server Layer** (`server.py`): Main MCP server implementation that handles tool registration and request routing
2. **Tools Layer** (`tools/`): Individual MCP tools for paper operations:
   - `search.py`: Advanced arXiv paper search with filtering
   - `download.py`: Paper download and storage management  
   - `list_papers.py`: List locally stored papers
   - `read_paper.py`: Read paper content from storage
   - `get_abstract.py`: Get paper abstract/metadata via arXiv API without downloading (token-saving alternative to download+read)
3. **Resource Management** (`resources/papers.py`): `PaperManager` class handles paper storage, PDF-to-markdown conversion using pymupdf4llm, and local caching
4. **Configuration** (`config.py`): Pydantic-based settings with environment variable support

### Key Design Patterns

- **MCP Protocol Compliance**: All tools follow MCP specification with proper type definitions
- **Async-First**: Built on asyncio with aiofiles for non-blocking I/O operations
- **Storage Strategy**: Papers downloaded as PDFs, converted to markdown, stored locally with PDF cleanup
- **Error Handling**: Comprehensive error handling with user-friendly messages throughout tool chain

### Configuration

Environment variables (all optional with sensible defaults):
- `ARXIV_STORAGE_PATH`: Paper storage location (default: `~/.arxiv-mcp-server/papers`)
- `ARXIV_MAX_RESULTS`: Search results limit (default: 50)
- `ARXIV_REQUEST_TIMEOUT`: API timeout in seconds (default: 60)

### Testing Strategy

Tests use pytest with async support and comprehensive mocking:
- `conftest.py` provides shared fixtures for mock arXiv papers and HTTP responses
- Tests cover both unit-level tool functionality and integration scenarios
- Mock-based approach avoids external API calls during testing