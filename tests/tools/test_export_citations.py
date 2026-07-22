"""Tests for the export_citations (BibTeX) tool — issue #41.

Network is mocked: handle_export_citations is exercised with a stubbed
_fetch_metadata so the citation logic is tested deterministically.
"""

import json

import pytest

from arxiv_mcp_server.tools import export_citations as ec


def _paper(pid, title, authors, published="2024-01-15T00:00:00Z", categories=("cs.AI",)):
    return {
        "id": pid,
        "title": title,
        "authors": list(authors),
        "abstract": "[EXTERNAL CONTENT] x",
        "categories": list(categories),
        "published": published,
        "url": f"https://arxiv.org/pdf/{pid}",
        "resource_uri": f"arxiv://{pid}",
    }


def _stub_metadata(monkeypatch, papers, recorder=None):
    """Patch _fetch_metadata to return canned metadata keyed by bare ID."""

    async def _fake(ids):
        # Mirror the real fetch: arXiv metadata is keyed by the *bare* ID
        # (the Atom parser strips version suffixes), regardless of the version
        # the caller queried with.
        if recorder is not None:
            recorder.extend(ids)
        bases = {ec._base_id(i) for i in ids}
        return {p["id"]: p for p in papers if p["id"] in bases}

    monkeypatch.setattr(ec, "_fetch_metadata", _fake)


async def _run(arguments):
    result = await ec.handle_export_citations(arguments)
    assert len(result) == 1 and result[0].type == "text"
    return json.loads(result[0].text)


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #

def test_bibtex_escape_special_characters():
    assert ec._bibtex_escape("Cost & Effect 50% #1 a_b") == r"Cost \& Effect 50\% \#1 a\_b"
    assert ec._bibtex_escape("a{b}c") == r"a\{b\}c"
    assert ec._bibtex_escape(r"back\slash") == r"back\textbackslash{}slash"
    assert ec._bibtex_escape("tilde~caret^") == r"tilde\textasciitilde{}caret\textasciicircum{}"


def test_citation_key_is_deterministic():
    authors = ["Ada Lovelace", "Alan Turing"]
    k1 = ec._citation_key(authors, "1936", "On Computable Numbers")
    k2 = ec._citation_key(authors, "1936", "On Computable Numbers")
    assert k1 == k2 == "lovelace1936on"


def test_citation_key_folds_accents_and_falls_back():
    assert ec._citation_key(["Erdős Pál"], "1949", "Prime Gaps") == "pal1949prime"
    assert ec._citation_key([], "", "") == "arxiv"


def test_base_id_strips_version_only():
    assert ec._base_id("2401.12345v2") == "2401.12345"
    assert ec._base_id("2401.12345") == "2401.12345"
    assert ec._base_id("hep-ph/9901234v3") == "hep-ph/9901234"


# --------------------------------------------------------------------------- #
# Tool behaviour                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_multiple_authors_joined_with_and(monkeypatch):
    _stub_metadata(
        monkeypatch,
        [_paper("2401.00001", "A Study", ["Ada Lovelace", "Alan Turing", "Grace Hopper"])],
    )
    payload = await _run({"paper_ids": ["2401.00001"]})
    assert payload["status"] == "success"
    assert "author = {Ada Lovelace and Alan Turing and Grace Hopper}" in payload["bibtex"]


@pytest.mark.asyncio
async def test_escaped_bibtex_characters(monkeypatch):
    _stub_metadata(
        monkeypatch,
        [_paper("2401.00002", "Cost & Effect: 50% of #1 in a_b", ["Jane Q. Smith"])],
    )
    payload = await _run({"paper_ids": ["2401.00002"]})
    entry = payload["results"][0]["bibtex"]
    assert r"title = {Cost \& Effect: 50\% of \#1 in a\_b}" in entry


@pytest.mark.asyncio
async def test_missing_optional_fields(monkeypatch):
    # No categories and no usable year -> those fields omitted, entry still valid.
    _stub_metadata(
        monkeypatch,
        [_paper("2401.00003", "No Extras", ["Solo Author"], published="", categories=[])],
    )
    payload = await _run({"paper_ids": ["2401.00003"]})
    entry = payload["results"][0]["bibtex"]
    assert "primaryClass" not in entry
    assert "year" not in entry
    assert "eprint = {2401.00003}" in entry
    assert "archivePrefix = {arXiv}" in entry


@pytest.mark.asyncio
async def test_versioned_id_preserved_in_eprint_and_url(monkeypatch):
    _stub_metadata(monkeypatch, [_paper("2401.00004", "Versioned", ["A B"])])
    payload = await _run({"paper_ids": ["2401.00004v2"]})
    entry = payload["results"][0]["bibtex"]
    assert "eprint = {2401.00004v2}" in entry
    assert "url = {https://arxiv.org/abs/2401.00004v2}" in entry


@pytest.mark.asyncio
async def test_legacy_id(monkeypatch):
    _stub_metadata(
        monkeypatch,
        [_paper("hep-ph/9901234", "Legacy Paper", ["Old Author"], published="1999-01-01T00:00:00Z")],
    )
    payload = await _run({"paper_ids": ["hep-ph/9901234"]})
    result = payload["results"][0]
    assert result["status"] == "success"
    assert result["key"] == "author1999legacy"
    assert "eprint = {hep-ph/9901234}" in result["bibtex"]


@pytest.mark.asyncio
async def test_invalid_id_not_fetched(monkeypatch):
    requested = []
    _stub_metadata(monkeypatch, [_paper("2401.00001", "Valid", ["A B"])], recorder=requested)
    payload = await _run({"paper_ids": ["not-an-id", "2401.00001"]})
    assert requested == ["2401.00001"]  # invalid ID never hit the network
    statuses = {r["paper_id"]: r["status"] for r in payload["results"]}
    assert statuses["not-an-id"] == "error"
    assert statuses["2401.00001"] == "success"
    assert payload["status"] == "partial"


@pytest.mark.asyncio
async def test_not_found_on_arxiv(monkeypatch):
    _stub_metadata(monkeypatch, [])  # well-formed but arXiv returns nothing
    payload = await _run({"paper_ids": ["2401.99999"]})
    assert payload["status"] == "error"
    assert payload["results"][0]["error"] == "not found on arXiv"


@pytest.mark.asyncio
async def test_multiple_paper_output_mixed(monkeypatch):
    _stub_metadata(
        monkeypatch,
        [
            _paper("2401.00001", "First", ["Ann Lee"]),
            _paper("2401.00002", "Second", ["Bob Ng"]),
        ],
    )
    payload = await _run({"paper_ids": ["2401.00001", "bad id", "2401.00002", "2401.77777"]})
    assert payload["count"] == {"requested": 4, "succeeded": 2, "failed": 2}
    assert payload["status"] == "partial"
    # Rendered BibTeX contains exactly the two successful entries.
    assert payload["bibtex"].count("@misc{") == 2
    # Results preserve request order.
    assert [r["paper_id"] for r in payload["results"]] == [
        "2401.00001",
        "bad id",
        "2401.00002",
        "2401.77777",
    ]


@pytest.mark.asyncio
async def test_duplicate_keys_disambiguated(monkeypatch):
    # Same surname + year + first title word -> keys must stay unique & deterministic.
    _stub_metadata(
        monkeypatch,
        [
            _paper("2401.00001", "Networks Rise", ["Sam Ford"]),
            _paper("2401.00002", "Networks Fall", ["Sam Ford"]),
        ],
    )
    payload = await _run({"paper_ids": ["2401.00001", "2401.00002"]})
    keys = [r["key"] for r in payload["results"]]
    assert keys == ["ford2024networks", "ford2024networksa"]
    assert len(set(keys)) == 2


@pytest.mark.asyncio
async def test_empty_input_is_error(monkeypatch):
    _stub_metadata(monkeypatch, [])
    payload = await _run({"paper_ids": []})
    assert payload["status"] == "error"


@pytest.mark.asyncio
async def test_too_many_ids_rejected(monkeypatch):
    _stub_metadata(monkeypatch, [])
    payload = await _run({"paper_ids": [f"2401.{i:05d}" for i in range(ec.MAX_IDS + 1)]})
    assert payload["status"] == "error"
    assert "max" in payload["message"]


def test_tool_registered_in_server():
    from arxiv_mcp_server.tools import export_citations_tool, handle_export_citations

    assert export_citations_tool.name == "export_citations"
    assert callable(handle_export_citations)
