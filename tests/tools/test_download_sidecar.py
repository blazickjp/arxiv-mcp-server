"""Sidecar metadata must come from the arXiv API, not HTML scraping (#176)."""

import json

import pytest

from arxiv_mcp_server.tools.download import (
    EXTRACTOR_VERSION,
    _html_to_text,
    handle_download,
)

SPLIT_TITLE_HTML = """
<html>
  <body>
    <article class="ltx_document">
      <h1 class="ltx_title ltx_title_document">Cacheable by Design? Training Mixture-of-Experts Routers
<br class="ltx_break">for Locality Against the Edge Memory-Bandwidth Wall
<br class="ltx_break">A Pre-Registered Negative Result, with a Systems Measurement Study</h1>
      <div class="ltx_authors">
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Shriniwas Ramesh Suram</span>
        </span>
      </div>
      <div class="ltx_abstract"><h6>Abstract</h6><p>Paper abstract body.</p></div>
    </article>
  </body>
</html>
"""

API_TITLE = (
    "Cacheable by Design? Training Mixture-of-Experts Routers "
    "for Locality Against the Edge Memory-Bandwidth Wall "
    "A Pre-Registered Negative Result, with a Systems Measurement Study"
)
HTML_FIRST_LINE = "Cacheable by Design? Training Mixture-of-Experts Routers"


def _patch_path(mocker, storage):
    mocker.patch(
        "arxiv_mcp_server.tools.download.get_paper_path",
        side_effect=lambda pid, suffix=".md": storage / f"{pid}{suffix}",
    )


def test_2608_shaped_html_title_is_joined_across_breaks():
    """EXTRACTOR_VERSION>=6 joins br-split document titles into one line (#258).

    Sidecar metadata still must come from the API (#176); see tests below.
    """
    text = _html_to_text(SPLIT_TITLE_HTML)
    assert text.splitlines()[0] == API_TITLE
    # Pre-#258 failure mode was first-line-only truncation.
    assert text.splitlines()[0] != HTML_FIRST_LINE


@pytest.mark.asyncio
async def test_sidecar_uses_api_metadata_not_html_title(temp_storage_path, mocker):
    """Sidecar title/authors/published come from the arXiv API result."""
    paper_id = "2608.18261"
    _patch_path(mocker, temp_storage_path)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=_html_to_text(SPLIT_TITLE_HTML),
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value={
            "title": API_TITLE,
            "authors": ["Shriniwas Ramesh Suram"],
            "published": "2026-08-20T00:00:00+00:00",
        },
    )
    mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)
    assert result["status"] == "success"

    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["title"] == API_TITLE
    assert sidecar["title"] != HTML_FIRST_LINE
    assert sidecar["authors"] == ["Shriniwas Ramesh Suram"]
    assert sidecar["published"] == "2026-08-20T00:00:00+00:00"
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION


@pytest.mark.asyncio
async def test_sidecar_nulls_when_api_fails_instead_of_html_scrape(
    temp_storage_path, mocker
):
    """If the API lookup fails, leave title/authors/published null — do not scrape."""
    paper_id = "2608.18261"
    _patch_path(mocker, temp_storage_path)
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=_html_to_text(SPLIT_TITLE_HTML),
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value=None,
    )
    mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id})
    result = json.loads(response[0].text)
    assert result["status"] == "success"

    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["id"] == paper_id
    assert sidecar["title"] is None
    assert sidecar["authors"] == []
    assert sidecar["published"] is None


@pytest.mark.asyncio
async def test_force_refresh_rewrites_sidecar_from_api(temp_storage_path, mocker):
    """force=true overwrites a truncated HTML sidecar with API metadata."""
    paper_id = "2608.18261"
    _patch_path(mocker, temp_storage_path)
    (temp_storage_path / f"{paper_id}.md").write_text(HTML_FIRST_LINE, encoding="utf-8")
    (temp_storage_path / f"{paper_id}.meta.json").write_text(
        json.dumps(
            {
                "id": paper_id,
                "title": HTML_FIRST_LINE,
                "authors": [],
                "published": None,
                "extractor_version": EXTRACTOR_VERSION,
            }
        ),
        encoding="utf-8",
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_html_content",
        return_value=_html_to_text(SPLIT_TITLE_HTML),
    )
    mocker.patch(
        "arxiv_mcp_server.tools.download._fetch_arxiv_metadata",
        return_value={
            "title": API_TITLE,
            "authors": ["Shriniwas Ramesh Suram"],
            "published": "2026-08-20T00:00:00+00:00",
        },
    )
    mocker.patch("arxiv_mcp_server.tools.download._fetch_pdf_content")

    response = await handle_download({"paper_id": paper_id, "force": True})
    result = json.loads(response[0].text)
    assert result["status"] == "success"

    sidecar = json.loads(
        (temp_storage_path / f"{paper_id}.meta.json").read_text(encoding="utf-8")
    )
    assert sidecar["title"] == API_TITLE
    assert sidecar["authors"] == ["Shriniwas Ramesh Suram"]
    assert sidecar["published"] == "2026-08-20T00:00:00+00:00"
