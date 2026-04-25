"""Tests for LaTeX source tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import mcp.types as types

from arxiv_mcp_server.tools.latex import (
    handle_get_paper_latex,
    handle_get_paper_latex_abstract,
    handle_list_paper_latex_sections,
    handle_get_paper_latex_section,
    get_paper_latex_tool,
    get_paper_latex_abstract_tool,
    list_paper_latex_sections_tool,
    get_paper_latex_section_tool,
)

FAKE_LATEX = r"""
\section{Introduction}
This is the introduction.

\section{Method}
\subsection{Details}
Some method details with $x = y^2$.
"""

FAKE_ABSTRACT = "This paper presents a new method."

FAKE_SECTIONS = ["1 Introduction", "2 Method", "2.1 Details"]


@pytest.fixture
def mock_arxiv_to_prompt():
    with (
        patch("arxiv_mcp_server.tools.latex.process_latex_source") as mock_process,
        patch("arxiv_mcp_server.tools.latex.list_sections") as mock_list,
        patch("arxiv_mcp_server.tools.latex.extract_section") as mock_extract,
    ):
        mock_process.return_value = FAKE_LATEX
        mock_list.return_value = FAKE_SECTIONS
        mock_extract.return_value = r"\section{Introduction}\nThis is the introduction."
        yield mock_process, mock_list, mock_extract


class TestToolDefinitions:
    def test_get_paper_latex_tool_schema(self):
        assert get_paper_latex_tool.name == "get_paper_latex"
        assert "arxiv_id" in get_paper_latex_tool.inputSchema["properties"]
        assert "arxiv_id" in get_paper_latex_tool.inputSchema["required"]

    def test_get_paper_latex_abstract_tool_schema(self):
        assert get_paper_latex_abstract_tool.name == "get_paper_latex_abstract"
        assert "arxiv_id" in get_paper_latex_abstract_tool.inputSchema["properties"]

    def test_list_paper_latex_sections_tool_schema(self):
        assert list_paper_latex_sections_tool.name == "list_paper_latex_sections"
        assert "arxiv_id" in list_paper_latex_sections_tool.inputSchema["properties"]

    def test_get_paper_latex_section_tool_schema(self):
        assert get_paper_latex_section_tool.name == "get_paper_latex_section"
        props = get_paper_latex_section_tool.inputSchema["properties"]
        required = get_paper_latex_section_tool.inputSchema["required"]
        assert "arxiv_id" in props
        assert "section_path" in props
        assert "arxiv_id" in required
        assert "section_path" in required


class TestGetPaperLatex:
    async def test_returns_full_latex(self, mock_arxiv_to_prompt):
        mock_process, _, _ = mock_arxiv_to_prompt
        result = await handle_get_paper_latex({"arxiv_id": "2403.12345"})
        assert len(result) == 1
        assert isinstance(result[0], types.TextContent)
        assert FAKE_LATEX in result[0].text
        assert "WARNING" in result[0].text
        assert "dollar sign" in result[0].text.lower() or "$" in result[0].text
        mock_process.assert_called_once_with("2403.12345")

    async def test_strips_whitespace_from_id(self, mock_arxiv_to_prompt):
        mock_process, _, _ = mock_arxiv_to_prompt
        await handle_get_paper_latex({"arxiv_id": "  2403.12345  "})
        mock_process.assert_called_once_with("2403.12345")

    async def test_missing_arxiv_id(self, mock_arxiv_to_prompt):
        result = await handle_get_paper_latex({})
        assert "Error" in result[0].text
        assert "arxiv_id" in result[0].text

    async def test_empty_arxiv_id(self, mock_arxiv_to_prompt):
        result = await handle_get_paper_latex({"arxiv_id": ""})
        assert "Error" in result[0].text

    async def test_process_error(self, mock_arxiv_to_prompt):
        mock_process, _, _ = mock_arxiv_to_prompt
        mock_process.side_effect = Exception("network error")
        result = await handle_get_paper_latex({"arxiv_id": "2403.12345"})
        assert "Error" in result[0].text
        assert "2403.12345" in result[0].text

    async def test_missing_deps(self):
        with patch("arxiv_mcp_server.tools.latex.process_latex_source", None):
            result = await handle_get_paper_latex({"arxiv_id": "2403.12345"})
            assert "[latex]" in result[0].text


class TestGetPaperLatexAbstract:
    async def test_returns_abstract(self, mock_arxiv_to_prompt):
        mock_process, _, _ = mock_arxiv_to_prompt
        mock_process.return_value = FAKE_ABSTRACT
        result = await handle_get_paper_latex_abstract({"arxiv_id": "2403.12345"})
        assert FAKE_ABSTRACT in result[0].text
        assert "WARNING" in result[0].text
        # abstract_only=True must be passed
        call_args = mock_process.call_args
        assert call_args.kwargs.get("abstract_only") is True or (
            len(call_args.args) > 1 and call_args.args[1] is True
        )

    async def test_missing_arxiv_id(self, mock_arxiv_to_prompt):
        result = await handle_get_paper_latex_abstract({})
        assert "Error" in result[0].text

    async def test_missing_deps(self):
        with patch("arxiv_mcp_server.tools.latex.process_latex_source", None):
            result = await handle_get_paper_latex_abstract({"arxiv_id": "2403.12345"})
            assert "[latex]" in result[0].text


class TestListPaperLatexSections:
    async def test_returns_sections(self, mock_arxiv_to_prompt):
        mock_process, mock_list, _ = mock_arxiv_to_prompt
        result = await handle_list_paper_latex_sections({"arxiv_id": "2403.12345"})
        text = result[0].text
        for section in FAKE_SECTIONS:
            assert section in text

    async def test_empty_sections(self, mock_arxiv_to_prompt):
        mock_process, mock_list, _ = mock_arxiv_to_prompt
        mock_list.return_value = []
        result = await handle_list_paper_latex_sections({"arxiv_id": "2403.12345"})
        assert "No sections found" in result[0].text

    async def test_missing_arxiv_id(self, mock_arxiv_to_prompt):
        result = await handle_list_paper_latex_sections({})
        assert "Error" in result[0].text

    async def test_missing_deps(self):
        with patch("arxiv_mcp_server.tools.latex.process_latex_source", None):
            result = await handle_list_paper_latex_sections({"arxiv_id": "2403.12345"})
            assert "[latex]" in result[0].text


class TestGetPaperLatexSection:
    async def test_returns_section(self, mock_arxiv_to_prompt):
        mock_process, _, mock_extract = mock_arxiv_to_prompt
        result = await handle_get_paper_latex_section(
            {"arxiv_id": "2403.12345", "section_path": "1"}
        )
        assert "WARNING" in result[0].text
        mock_extract.assert_called_once_with(FAKE_LATEX, "1")

    async def test_section_not_found(self, mock_arxiv_to_prompt):
        mock_process, _, mock_extract = mock_arxiv_to_prompt
        mock_extract.return_value = None
        result = await handle_get_paper_latex_section(
            {"arxiv_id": "2403.12345", "section_path": "99"}
        )
        assert "not found" in result[0].text
        assert "list_paper_latex_sections" in result[0].text

    async def test_missing_arxiv_id(self, mock_arxiv_to_prompt):
        result = await handle_get_paper_latex_section({"section_path": "1"})
        assert "Error" in result[0].text
        assert "arxiv_id" in result[0].text

    async def test_missing_section_path(self, mock_arxiv_to_prompt):
        result = await handle_get_paper_latex_section({"arxiv_id": "2403.12345"})
        assert "Error" in result[0].text
        assert "section_path" in result[0].text

    async def test_process_error(self, mock_arxiv_to_prompt):
        mock_process, _, _ = mock_arxiv_to_prompt
        mock_process.side_effect = RuntimeError("source unavailable")
        result = await handle_get_paper_latex_section(
            {"arxiv_id": "2403.12345", "section_path": "1"}
        )
        assert "Error" in result[0].text

    async def test_missing_deps(self):
        with patch("arxiv_mcp_server.tools.latex.process_latex_source", None):
            result = await handle_get_paper_latex_section(
                {"arxiv_id": "2403.12345", "section_path": "1"}
            )
            assert "[latex]" in result[0].text
