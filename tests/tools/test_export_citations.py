"""Tests for the export_citations (BibTeX) tool — issue #41.

Network is mocked: handle_export_citations is exercised with a stubbed
_fetch_metadata so the citation logic is tested deterministically.
"""

import json

import pytest

from arxiv_mcp_server.tools import export_citations as ec


def _paper(
    pid, title, authors, published="2024-01-15T00:00:00Z", categories=("cs.AI",)
):
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
    assert (
        ec._bibtex_escape("Cost & Effect 50% #1 a_b") == r"Cost \& Effect 50\% \#1 a\_b"
    )
    assert ec._bibtex_escape("a{b}c") == r"a\{b\}c"
    assert ec._bibtex_escape(r"back\slash") == r"back\textbackslash{}slash"
    assert (
        ec._bibtex_escape("tilde~caret^")
        == r"tilde\textasciitilde{}caret\textasciicircum{}"
    )


def test_citation_key_is_deterministic():
    authors = ["Ada Lovelace", "Alan Turing"]
    k1 = ec._citation_key(authors, "1936", "On Computable Numbers")
    k2 = ec._citation_key(authors, "1936", "On Computable Numbers")
    assert k1 == k2 == "lovelace1936on"


def test_citation_key_folds_accents_and_falls_back():
    assert ec._citation_key(["Erdős Pál"], "1949", "Prime Gaps") == "pal1949prime"
    assert ec._citation_key([], "", "") == "arxiv"


def test_alpha_suffix_stays_alphabetic_past_z():
    """chr(ord('a') + n) would emit '{', '|', '}' past 26 — invalid in a key."""
    assert ec._alpha_suffix(1) == "a"
    assert ec._alpha_suffix(26) == "z"
    assert ec._alpha_suffix(27) == "aa"
    assert ec._alpha_suffix(52) == "az"
    assert all(ec._alpha_suffix(i).isalpha() for i in range(1, 200))


def test_unique_key_disambiguates_beyond_26_collisions():
    used: set = set()
    keys = []
    for _ in range(30):
        key = ec._unique_key("smith2024the", used)
        used.add(key)
        keys.append(key)
    assert len(set(keys)) == 30
    # keys[0] is the unsuffixed base, so 'a'..'z' occupy 1..26 and 'aa' starts at 27.
    assert keys[26] == "smith2024thez"
    assert keys[27:30] == ["smith2024theaa", "smith2024theab", "smith2024theac"]
    # Every key must be a legal BibTeX citation key.
    assert all(k.isalnum() for k in keys)


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
        [
            _paper(
                "2401.00001", "A Study", ["Ada Lovelace", "Alan Turing", "Grace Hopper"]
            )
        ],
    )
    payload = await _run({"paper_ids": ["2401.00001"]})
    assert payload["status"] == "success"
    assert (
        "author = {Ada Lovelace and Alan Turing and Grace Hopper}" in payload["bibtex"]
    )


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
        [
            _paper(
                "2401.00003", "No Extras", ["Solo Author"], published="", categories=[]
            )
        ],
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
        [
            _paper(
                "hep-ph/9901234",
                "Legacy Paper",
                ["Old Author"],
                published="1999-01-01T00:00:00Z",
            )
        ],
    )
    payload = await _run({"paper_ids": ["hep-ph/9901234"]})
    result = payload["results"][0]
    assert result["status"] == "success"
    assert result["key"] == "author1999legacy"
    assert "eprint = {hep-ph/9901234}" in result["bibtex"]


@pytest.mark.asyncio
async def test_invalid_id_not_fetched(monkeypatch):
    requested = []
    _stub_metadata(
        monkeypatch, [_paper("2401.00001", "Valid", ["A B"])], recorder=requested
    )
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
async def test_all_failed_batch_is_mcp_error_but_keeps_diagnostics(monkeypatch):
    """An all-failed batch surfaces as isError, per the not-found convention.

    Guards the other half of that contract: the JSON body must survive into the
    error content so a client can still read per-paper diagnostics. Exercised
    at the protocol level, since calling the handler directly bypasses dispatch.
    """
    import mcp.types as types
    from arxiv_mcp_server import server as server_module

    _stub_metadata(monkeypatch, [])
    handler = server_module.server.request_handlers[types.CallToolRequest]
    result = await handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="export_citations", arguments={"paper_ids": ["2401.99999"]}
            )
        )
    )

    assert result.root.isError is True
    payload = json.loads(result.root.content[0].text)
    assert payload["status"] == "error"
    assert payload["count"] == {"requested": 1, "succeeded": 0, "failed": 1}
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
    payload = await _run(
        {"paper_ids": ["2401.00001", "bad id", "2401.00002", "2401.77777"]}
    )
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
    payload = await _run(
        {"paper_ids": [f"2401.{i:05d}" for i in range(ec.MAX_IDS + 1)]}
    )
    assert payload["status"] == "error"
    assert "max" in payload["message"]


async def test_tool_registered_in_server():
    """Assert against the server's advertised tool list, not just the export."""
    from arxiv_mcp_server.server import list_tools

    tools = {tool.name: tool for tool in await list_tools()}
    assert "export_citations" in tools
    assert tools["export_citations"].inputSchema["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# RIS and CSL-JSON (follow-up to #41: formats scoped out of the first PR)      #
# --------------------------------------------------------------------------- #


def test_arxiv_doi_derived_from_bare_id():
    # The version suffix belongs to the citation, not to the DOI.
    assert ec._arxiv_doi("2401.12345v2") == "10.48550/arXiv.2401.12345"
    assert ec._arxiv_doi("hep-ph/9901234") == "10.48550/arXiv.hep-ph/9901234"


def test_one_line_collapses_wrapped_metadata():
    assert ec._one_line("Attention\n  Is All\tYou Need") == "Attention Is All You Need"


def test_split_name_family_and_given():
    assert ec._split_name("Ashish Vaswani") == ("Vaswani", "Ashish")
    assert ec._split_name("Jean-Luc de la Fontaine") == ("Fontaine", "Jean-Luc de la")
    assert ec._split_name("Collaboration") == ("", "")
    # Corporate authors are not people: they must not be split into given/family.
    assert ec._split_name("LIGO Scientific Collaboration") == ("", "")


@pytest.mark.asyncio
async def test_ris_record_structure(monkeypatch):
    _stub_metadata(
        monkeypatch,
        [_paper("2401.00001", "Deep Nets", ["Ada Lovelace", "Alan Turing"])],
    )
    payload = await _run({"paper_ids": ["2401.00001"], "format": "ris"})

    assert payload["status"] == "success"
    assert payload["format"] == "ris"
    record = payload["ris"]
    lines = record.split("\n")
    assert lines[0] == "TY  - GEN"
    assert lines[-1] == "ER  - "
    assert "AU  - Ada Lovelace" in lines
    assert "AU  - Alan Turing" in lines
    assert "TI  - Deep Nets" in lines
    assert "PY  - 2024" in lines
    assert "DA  - 2024/01/15" in lines
    assert "PB  - arXiv" in lines
    assert "DO  - 10.48550/arXiv.2401.00001" in lines
    assert "UR  - https://arxiv.org/abs/2401.00001" in lines
    assert f"ID  - {payload['results'][0]['key']}" in lines
    # Every line is a tag line; nothing wrapped across lines.
    assert all(line[:2].isalpha() and line[2:6] == "  - " for line in lines)


@pytest.mark.asyncio
async def test_ris_preserves_requested_version_in_url_only(monkeypatch):
    _stub_metadata(monkeypatch, [_paper("2401.00002", "Versioned", ["Grace Hopper"])])
    payload = await _run({"paper_ids": ["2401.00002v3"], "format": "ris"})

    assert "UR  - https://arxiv.org/abs/2401.00002v3" in payload["ris"]
    assert "DO  - 10.48550/arXiv.2401.00002" in payload["ris"]


@pytest.mark.asyncio
async def test_csl_json_items(monkeypatch):
    _stub_metadata(
        monkeypatch,
        [
            _paper(
                "2401.00003",
                "On Categories\nand Functors",
                ["Emmy Noether", "LIGO Collaboration"],
                categories=("math.CT", "cs.LO"),
            )
        ],
    )
    payload = await _run({"paper_ids": ["2401.00003"], "format": "csl-json"})

    assert payload["format"] == "csl-json"
    items = payload["csl"]
    assert isinstance(items, list) and len(items) == 1
    item = items[0]
    # CSL has no "preprint" item type; "article" + genre is the faithful mapping.
    assert item["type"] == "article"
    assert item["genre"] == "preprint"
    assert item["title"] == "On Categories and Functors"
    assert item["author"][0] == {"family": "Noether", "given": "Emmy"}
    assert item["author"][1] == {"literal": "LIGO Collaboration"}
    assert item["issued"] == {"date-parts": [[2024, 1, 15]]}
    assert item["DOI"] == "10.48550/arXiv.2401.00003"
    assert item["URL"] == "https://arxiv.org/abs/2401.00003"
    assert item["publisher"] == "arXiv"
    assert item["categories"] == ["math.CT", "cs.LO"]
    assert item["id"] == payload["results"][0]["key"]


@pytest.mark.asyncio
async def test_default_format_stays_bibtex(monkeypatch):
    _stub_metadata(monkeypatch, [_paper("2401.00004", "Unchanged", ["Ada Lovelace"])])
    payload = await _run({"paper_ids": ["2401.00004"]})

    assert payload["format"] == "bibtex"
    assert payload["bibtex"].startswith("@misc{")
    assert "ris" not in payload and "csl" not in payload


@pytest.mark.asyncio
async def test_unsupported_format_is_rejected(monkeypatch):
    _stub_metadata(monkeypatch, [_paper("2401.00005", "Nope", ["Ada Lovelace"])])
    payload = await _run({"paper_ids": ["2401.00005"], "format": "endnote"})

    assert payload["status"] == "error"
    assert "unsupported format" in payload["message"]


@pytest.mark.asyncio
async def test_partial_batch_keeps_per_paper_status_in_ris(monkeypatch):
    _stub_metadata(monkeypatch, [_paper("2401.00006", "Present", ["Ada Lovelace"])])
    payload = await _run(
        {"paper_ids": ["2401.00006", "2401.99999", "not-an-id"], "format": "ris"}
    )

    assert payload["status"] == "partial"
    assert payload["count"] == {"requested": 3, "succeeded": 1, "failed": 2}
    statuses = [r["status"] for r in payload["results"]]
    assert statuses == ["success", "error", "error"]
