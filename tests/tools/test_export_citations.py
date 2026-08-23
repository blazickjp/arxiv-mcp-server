"""Tests for the export_citations (BibTeX) tool — issue #41.

Network is mocked: handle_export_citations is exercised with a stubbed
_fetch_metadata so the citation logic is tested deterministically.
"""

import json

import pytest

from arxiv_mcp_server.tools import export_citations as ec


def _paper(
    pid,
    title,
    authors,
    published="2024-01-15T00:00:00Z",
    categories=("cs.AI",),
    versioned_id=None,
):
    entry = {
        "id": pid,
        "title": title,
        "authors": list(authors),
        "abstract": "[EXTERNAL CONTENT] x",
        "categories": list(categories),
        "published": published,
        "url": f"https://arxiv.org/pdf/{pid}",
        "resource_uri": f"arxiv://{pid}",
    }
    if versioned_id is not None:
        entry["versioned_id"] = versioned_id
    return entry


def _stub_metadata(monkeypatch, papers, recorder=None):
    """Patch _fetch_metadata to mirror versioned + bare→latest indexing (#212)."""

    async def _fake(ids):
        # Mirror the real fetch: entries are keyed by versioned_id, with an
        # additional bare-id → latest-version mapping among returned papers.
        if recorder is not None:
            recorder.extend(ids)
        bases = {ec._base_id(i) for i in ids}
        matching = [p for p in papers if p["id"] in bases]
        by_key = {}
        latest_by_bare = {}
        for p in matching:
            versioned = p.get("versioned_id") or p["id"]
            by_key[versioned] = p
            existing = latest_by_bare.get(p["id"])
            if existing is None:
                latest_by_bare[p["id"]] = p
            else:
                from arxiv_mcp_server.tools.arxiv_ids import arxiv_version_number

                if arxiv_version_number(versioned) > arxiv_version_number(
                    existing.get("versioned_id") or ""
                ):
                    latest_by_bare[p["id"]] = p
        by_key.update(latest_by_bare)
        return by_key

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
    _stub_metadata(
        monkeypatch,
        [_paper("2401.00004", "Versioned", ["A B"], versioned_id="2401.00004v2")],
    )
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


@pytest.mark.asyncio
async def test_nonexistent_version_not_found(monkeypatch):
    """Unknown version must not succeed with an abs URL (#196)."""
    _stub_metadata(monkeypatch, [])
    payload = await _run({"paper_ids": ["2307.09288v999"]})
    assert payload["status"] == "error"
    assert payload["results"][0]["paper_id"] == "2307.09288v999"
    assert payload["results"][0]["error"] == "not found on arXiv"
    assert payload["bibtex"] == ""


@pytest.mark.asyncio
async def test_nonexistent_version_rejected_when_other_version_exists(monkeypatch):
    """Base metadata for another version must not satisfy a bad version request."""

    async def _fake(ids):
        paper = _paper(
            "2307.09288", "Llama 2", ["Meta AI"], versioned_id="2307.09288v1"
        )
        # Real _fetch_metadata keys by versioned_id and bare→latest.
        return {"2307.09288v1": paper, "2307.09288": paper}

    monkeypatch.setattr(ec, "_fetch_metadata", _fake)
    payload = await _run({"paper_ids": ["2307.09288v1", "2307.09288v999"]})
    assert payload["status"] == "partial"
    by_id = {r["paper_id"]: r for r in payload["results"]}
    assert by_id["2307.09288v1"]["status"] == "success"
    assert "2307.09288v1" in by_id["2307.09288v1"]["bibtex"]
    assert by_id["2307.09288v999"]["status"] == "error"
    assert by_id["2307.09288v999"]["error"] == "not found on arXiv"


@pytest.mark.asyncio
async def test_batch_multiple_versions_of_same_paper(monkeypatch):
    """Batching v7 and v1 of one paper must succeed for both (#212).

    Previously _fetch_metadata keyed by bare id so the last Atom entry won and
    the other requested version was reported as not found.
    """
    v7 = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2023-08-02T00:00:00Z",
        versioned_id="1706.03762v7",
    )
    v1 = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2017-06-12T00:00:00Z",
        versioned_id="1706.03762v1",
    )

    async def _fake(ids):
        # Simulate Atom returning both versioned entries for the batch query.
        assert ids == ["1706.03762v7", "1706.03762v1"]
        # Build the same indexing the real _fetch_metadata produces.
        by_key = {"1706.03762v7": v7, "1706.03762v1": v1, "1706.03762": v7}
        return by_key

    monkeypatch.setattr(ec, "_fetch_metadata", _fake)
    payload = await _run({"paper_ids": ["1706.03762v7", "1706.03762v1"]})
    assert payload["status"] == "success"
    assert payload["count"] == {"requested": 2, "succeeded": 2, "failed": 0}
    by_id = {r["paper_id"]: r for r in payload["results"]}
    assert by_id["1706.03762v7"]["status"] == "success"
    assert by_id["1706.03762v1"]["status"] == "success"
    assert "eprint = {1706.03762v7}" in by_id["1706.03762v7"]["bibtex"]
    assert "eprint = {1706.03762v1}" in by_id["1706.03762v1"]["bibtex"]


@pytest.mark.asyncio
async def test_bare_id_resolves_to_latest_when_versions_batched(monkeypatch):
    """Bare id still maps to the latest version among returned entries."""
    v7 = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2023-08-02T00:00:00Z",
        versioned_id="1706.03762v7",
    )
    v1 = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2017-06-12T00:00:00Z",
        versioned_id="1706.03762v1",
    )

    async def _fake(ids):
        return {"1706.03762v7": v7, "1706.03762v1": v1, "1706.03762": v7}

    monkeypatch.setattr(ec, "_fetch_metadata", _fake)
    payload = await _run({"paper_ids": ["1706.03762"]})
    assert payload["status"] == "success"
    assert payload["results"][0]["status"] == "success"
    assert "eprint = {1706.03762}" in payload["results"][0]["bibtex"]


@pytest.mark.asyncio
async def test_bare_and_versioned_same_paper_one_bibtex(monkeypatch):
    """Bare + versioned of the same paper emit one BibTeX entry (#241).

    Prefer the versioned id when mixed; either order must collapse to one entry.
    Distinct versioned ids of the same paper remain separate (#212).
    """
    paper = _paper(
        "2410.17954",
        "ExpertFlow: Adaptive Expert Scheduling",
        ["Yuan He"],
        published="2024-10-23T00:00:00Z",
        versioned_id="2410.17954v2",
    )
    _stub_metadata(monkeypatch, [paper])

    for paper_ids in (
        ["2410.17954", "2410.17954v2"],
        ["2410.17954v2", "2410.17954"],
    ):
        payload = await _run({"paper_ids": paper_ids})
        assert payload["status"] == "success", paper_ids
        assert payload["bibtex"].count("@misc{") == 1, paper_ids
        assert payload["count"]["succeeded"] == 1, paper_ids
        assert payload["count"]["failed"] == 0, paper_ids
        assert len(payload["results"]) == 1, paper_ids
        assert payload["results"][0]["paper_id"] == "2410.17954v2"
        assert "eprint = {2410.17954v2}" in payload["bibtex"]
        assert "he2024expertflowa" not in payload["bibtex"]
        assert payload["results"][0]["key"] == "he2024expertflow"


@pytest.mark.asyncio
async def test_bare_dropped_when_two_versions_also_requested(monkeypatch):
    """Bare + v1 + v7 keeps both versions, drops bare (#241 + #212)."""
    v7 = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2023-08-02T00:00:00Z",
        versioned_id="1706.03762v7",
    )
    v1 = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2017-06-12T00:00:00Z",
        versioned_id="1706.03762v1",
    )

    async def _fake(ids):
        return {"1706.03762v7": v7, "1706.03762v1": v1, "1706.03762": v7}

    monkeypatch.setattr(ec, "_fetch_metadata", _fake)
    payload = await _run({"paper_ids": ["1706.03762", "1706.03762v7", "1706.03762v1"]})
    assert payload["status"] == "success"
    assert payload["bibtex"].count("@misc{") == 2
    assert [r["paper_id"] for r in payload["results"]] == [
        "1706.03762v7",
        "1706.03762v1",
    ]


@pytest.mark.asyncio
async def test_exact_duplicate_bare_ids_one_bibtex(monkeypatch):
    """Identical bare IDs collapse to one BibTeX entry (#259)."""
    _stub_metadata(
        monkeypatch,
        [
            _paper(
                "1706.03762",
                "Attention Is All You Need",
                ["Ashish Vaswani"],
                published="2017-06-12T00:00:00Z",
            )
        ],
    )
    payload = await _run({"paper_ids": ["1706.03762", "1706.03762"]})
    assert payload["status"] == "success"
    assert payload["bibtex"].count("@misc{") == 1
    assert payload["count"]["succeeded"] == 1
    assert payload["count"]["failed"] == 0
    assert len(payload["results"]) == 1
    assert payload["results"][0]["paper_id"] == "1706.03762"
    assert "eprint = {1706.03762}" in payload["bibtex"]


@pytest.mark.asyncio
async def test_exact_duplicate_versioned_ids_one_bibtex(monkeypatch):
    """Identical versioned IDs (e.g. v7+v7) collapse to one BibTeX entry (#259)."""
    paper = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2023-08-02T00:00:00Z",
        versioned_id="1706.03762v7",
    )
    _stub_metadata(monkeypatch, [paper])
    payload = await _run({"paper_ids": ["1706.03762v7", "1706.03762v7"]})
    assert payload["status"] == "success"
    assert payload["bibtex"].count("@misc{") == 1
    assert payload["count"]["succeeded"] == 1
    assert payload["count"]["failed"] == 0
    assert len(payload["results"]) == 1
    assert payload["results"][0]["paper_id"] == "1706.03762v7"
    assert "eprint = {1706.03762v7}" in payload["bibtex"]


@pytest.mark.asyncio
async def test_exact_duplicates_keep_bare_versioned_prefer_versioned(monkeypatch):
    """Exact-dup collapse coexists with #241 bare+versioned prefer-versioned."""
    paper = _paper(
        "1706.03762",
        "Attention Is All You Need",
        ["Ashish Vaswani"],
        published="2023-08-02T00:00:00Z",
        versioned_id="1706.03762v7",
    )
    _stub_metadata(monkeypatch, [paper])
    payload = await _run(
        {
            "paper_ids": [
                "1706.03762",
                "1706.03762",
                "1706.03762v7",
                "1706.03762v7",
            ]
        }
    )
    assert payload["status"] == "success"
    assert payload["bibtex"].count("@misc{") == 1
    assert payload["count"]["succeeded"] == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["paper_id"] == "1706.03762v7"
    assert "eprint = {1706.03762v7}" in payload["bibtex"]


def test_bares_with_versioned_sibling_helper():
    assert ec._bares_with_versioned_sibling(["2410.17954", "2410.17954v2"]) == {
        "2410.17954"
    }
    assert ec._bares_with_versioned_sibling(["2410.17954v2", "2410.17954"]) == {
        "2410.17954"
    }
    assert ec._bares_with_versioned_sibling(["1706.03762v7", "1706.03762v1"]) == {
        "1706.03762"
    }
    assert ec._bares_with_versioned_sibling(["2401.00001", "2401.00002"]) == set()


@pytest.mark.asyncio
async def test_fetch_metadata_keeps_each_versioned_entry(monkeypatch):
    """Atom returning v7 and v1 must index both; bare maps to latest (#212)."""
    from unittest.mock import AsyncMock, MagicMock

    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>v7 abstract</summary>
    <published>2023-08-02T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <link title="pdf" href="https://arxiv.org/pdf/1706.03762v7"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v1</id>
    <title>Attention Is All You Need</title>
    <summary>v1 abstract</summary>
    <published>2017-06-12T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <link title="pdf" href="https://arxiv.org/pdf/1706.03762v1"/>
  </entry>
</feed>
"""
    response = MagicMock()
    response.text = atom
    monkeypatch.setattr(ec, "_rate_limited_get", AsyncMock(return_value=response))

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ec.httpx, "AsyncClient", _FakeClient)

    meta = await ec._fetch_metadata(["1706.03762v7", "1706.03762v1"])
    assert "1706.03762v7" in meta
    assert "1706.03762v1" in meta
    assert meta["1706.03762v7"]["versioned_id"] == "1706.03762v7"
    assert meta["1706.03762v1"]["versioned_id"] == "1706.03762v1"
    # Bare id resolves to the latest returned version.
    assert meta["1706.03762"]["versioned_id"] == "1706.03762v7"
