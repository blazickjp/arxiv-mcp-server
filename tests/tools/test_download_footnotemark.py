"""HTML extract must drop footnotemark artifacts and permission boilerplate (#177)."""

from arxiv_mcp_server.tools.download import EXTRACTOR_VERSION, _html_to_text

FOOTNOTEMARK_HTML = """
<html>
  <body>
    <article class="ltx_document">
      <p class="ltx_align_center">
        <span>Provided proper attribution is provided, Google hereby grants permission to reproduce the tables and figures in this paper solely for use in journalistic or scholarly works.</span>
      </p>
      <h1 class="ltx_title">Attention Is All You Need</h1>
      <div class="ltx_authors">
        <span class="ltx_personname">Ashish Vaswani</span>
        <span class="ltx_personname">Noam Shazeer<span id="footnotex1" class="ltx_note ltx_role_footnotemark"><sup class="ltx_note_mark">1</sup><span class="ltx_note_outer"><span class="ltx_note_content"><sup class="ltx_note_mark">1</sup><span class="ltx_note_type">footnotemark: </span><span class="ltx_tag ltx_tag_note">1</span></span></span></span></span>
      </div>
      <div class="ltx_abstract"><h6>Abstract</h6><p>The dominant sequence transduction models are based on attention.</p></div>
    </article>
  </body>
</html>
"""


def test_extractor_version_bumped_for_footnotemark_fix():
    """#175 auto-invalidates old Attention caches after this extractor change."""
    assert EXTRACTOR_VERSION >= 4


def test_html_to_text_drops_footnotemark_and_permission_line():
    text = _html_to_text(FOOTNOTEMARK_HTML)
    assert "Attention Is All You Need" in text
    assert "The dominant sequence transduction models" in text
    assert "Ashish Vaswani" in text
    assert "Noam Shazeer" in text
    assert "footnotemark" not in text.lower()
    assert "1 1 footnotemark: 1" not in text
    assert "permission to reproduce" not in text.lower()
    assert "tables and figures" not in text.lower()


def test_html_to_text_keeps_158_chrome_strips():
    """#158 nonprofit banner and report-issue dialog must stay gone."""
    html = """
    <html><body>
      <dialog id="modal-form"><p>Content selection saved. Describe the issue below:</p></dialog>
      <div class="ds-announcement" id="announcement-banner">arXiv is now an independent nonprofit!</div>
      <article>
        <h1 class="ltx_title">Attention Is All You Need</h1>
        <span class="ltx_personname">Ashish Vaswani</span>
        <span class="ltx_author_notes"><span class="ltx_contact">Thanks: ORCID</span></span>
        <p>Paper body sentence about attention.</p>
      </article>
    </body></html>
    """
    text = _html_to_text(html)
    assert "Paper body sentence about attention." in text
    assert "Ashish Vaswani" in text
    leaked = [
        "Content selection saved",
        "Describe the issue below",
        "arXiv is now an independent nonprofit",
        "Thanks:",
        "ORCID",
    ]
    for snippet in leaked:
        assert snippet not in text, snippet
