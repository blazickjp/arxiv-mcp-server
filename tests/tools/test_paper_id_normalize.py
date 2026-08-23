"""get_abstract / export_citations ID normalize + validate (issues #162, #166)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from arxiv_mcp_server.tools.get_abstract import handle_get_abstract
from arxiv_mcp_server.tools import export_citations as ec

FULL_ATOM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>We propose a novel model.</summary>
    <published>2017-06-12T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
    <arxiv:primary_category term="cs.CL"/>
    <link title="pdf" href="https://arxiv.org/pdf/1706.03762v7"/>
  </entry>
</feed>
"""


def _make_mock_response(xml_text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = xml_text
    return resp


def _mock_rate_limited_get(xml_text: str):
    return AsyncMock(return_value=_make_mock_response(xml_text))


def _paper(
    pid,
    title,
    authors,
    published="2017-06-12T00:00:00Z",
    categories=("cs.CL",),
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


@pytest.mark.asyncio
async def test_arxiv_prefix_id_normalizes_and_succeeds(mocker):
    """arxiv:1706.03762v7 is stripped to 1706.03762v7 before the API call."""
    mock_get = _mock_rate_limited_get(FULL_ATOM_XML)
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        mock_get,
    )
    result = await handle_get_abstract({"paper_id": "arxiv:1706.03762v7"})
    data = json.loads(result[0].text)
    assert data["status"] == "success"
    assert data["paper_id"] == "1706.03762v7"
    mock_get.assert_awaited_once()
    called_url = mock_get.await_args.args[1]
    assert "id_list=1706.03762v7" in called_url
    assert "arxiv:" not in called_url


@pytest.mark.asyncio
async def test_abs_url_normalizes_and_succeeds(mocker):
    """https://arxiv.org/abs/1706.03762 is extracted before the API call."""
    mock_get = _mock_rate_limited_get(FULL_ATOM_XML)
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        mock_get,
    )
    result = await handle_get_abstract({"paper_id": "https://arxiv.org/abs/1706.03762"})
    data = json.loads(result[0].text)
    assert data["status"] == "success"
    assert data["paper_id"] == "1706.03762"
    mock_get.assert_awaited_once()
    called_url = mock_get.await_args.args[1]
    assert "id_list=1706.03762" in called_url
    assert "arxiv.org/abs" not in called_url


@pytest.mark.asyncio
async def test_pdf_url_normalizes_and_succeeds(mocker):
    mock_get = _mock_rate_limited_get(FULL_ATOM_XML)
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        mock_get,
    )
    result = await handle_get_abstract(
        {"paper_id": "https://arxiv.org/pdf/1706.03762v7.pdf"}
    )
    data = json.loads(result[0].text)
    assert data["status"] == "success"
    assert data["paper_id"] == "1706.03762v7"
    called_url = mock_get.await_args.args[1]
    assert "id_list=1706.03762v7" in called_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paper_id",
    ["not-a-paper", "garbage!!!", "arxiv:not-a-paper", "just some text"],
)
async def test_invalid_paper_id_does_not_call_http(mocker, paper_id):
    """Garbage IDs return a clean format error and never hit the arXiv API."""
    mock_get = AsyncMock()
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        mock_get,
    )
    result = await handle_get_abstract({"paper_id": paper_id})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert data["message"] == "invalid arXiv ID format"
    assert "400" not in data["message"]
    assert "export.arxiv.org" not in data["message"]
    mock_get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_id", ["", "   "])
async def test_empty_paper_id_does_not_call_http(mocker, paper_id):
    mock_get = AsyncMock()
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        mock_get,
    )
    result = await handle_get_abstract({"paper_id": paper_id})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "400" not in data["message"]
    assert "export.arxiv.org" not in result[0].text
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_http_status_error_does_not_leak_upstream_url(mocker):
    import httpx

    request = httpx.Request("GET", "https://export.arxiv.org/api/query?id_list=x")
    response = httpx.Response(400, request=request)
    mocker.patch(
        "arxiv_mcp_server.tools.get_abstract._rate_limited_get",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request", request=request, response=response
            )
        ),
    )
    result = await handle_get_abstract({"paper_id": "1706.03762"})
    data = json.loads(result[0].text)
    assert data["status"] == "error"
    assert "400" not in data["message"]
    assert "export.arxiv.org" not in data["message"]
    assert "Bad Request" not in data["message"]


@pytest.mark.asyncio
async def test_export_citations_normalizes_wrappers(monkeypatch):
    """Wrappers normalize; bare+versioned collapse; invalid still reported (#162, #241)."""
    requested = []
    _stub_metadata(
        monkeypatch,
        [
            _paper(
                "1706.03762",
                "Attention",
                ["Ashish Vaswani"],
                versioned_id="1706.03762v7",
            )
        ],
        recorder=requested,
    )
    payload = await _run(
        {
            "paper_ids": [
                "arxiv:1706.03762v7",
                "https://arxiv.org/abs/1706.03762",
                "not-a-paper",
            ]
        }
    )
    # Fetch still sees both normalized valid ids; results drop superseded bare (#241).
    assert requested == ["1706.03762v7", "1706.03762"]
    assert payload["status"] == "partial"
    assert payload["count"]["requested"] == 3
    assert payload["count"]["succeeded"] == 1
    assert payload["count"]["failed"] == 1
    assert len(payload["results"]) == 2
    assert payload["results"][0]["status"] == "success"
    assert payload["results"][0]["paper_id"] == "1706.03762v7"
    assert "eprint = {1706.03762v7}" in payload["results"][0]["bibtex"]
    assert payload["bibtex"].count("@misc{") == 1
    assert payload["results"][1]["paper_id"] == "not-a-paper"
    assert payload["results"][1]["error"] == "invalid arXiv ID format"
