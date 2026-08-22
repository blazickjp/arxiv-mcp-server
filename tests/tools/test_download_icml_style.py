"""HTML extract must drop ICML/LaTeX page-layout style warnings (#190)."""

from arxiv_mcp_server.tools.download import EXTRACTOR_VERSION, _html_to_text

ICML_STYLE_WARNING_HTML = """
<html>
  <body>
    <article class="ltx_document">
      <div id="p1" class="ltx_para">
        <p id="p1.1" class="ltx_p">marginparsep has been altered.
        <br class="ltx_break"/>topmargin has been altered.
        <br class="ltx_break"/>marginparpush has been altered.
        <br class="ltx_break"/></p>
      </div>
      <div id="p2" class="ltx_para ltx_noindent">
        <p id="p2.1" class="ltx_p">
          <span class="ltx_text ltx_font_bold ltx_font_italic">
            The page layout violates the ICML style.
          </span>
        </p>
      </div>
      <div id="p3" class="ltx_para ltx_noindent">
        <p id="p3.1" class="ltx_p">Please do not change the page layout, or include packages like geometry,
        savetrees, or fullpage, which change it for you.</p>
      </div>
      <div id="p4" class="ltx_para ltx_noindent">
        <p id="p4.1" class="ltx_p">We're not able to reliably undo arbitrary changes to the style. Please remove
        the offending package(s), or layout-changing commands and try again.</p>
      </div>
      <div id="p5" class="ltx_para ltx_noindent ltx_align_center">
        <p id="p5.1" class="ltx_p">
          <span class="ltx_text ltx_font_bold">
            Why Has Predicting Downstream Capabilities of Frontier AI Models with Scale Remained Elusive?
          </span>
        </p>
      </div>
      <div id="abstract1" class="ltx_abstract">
        <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
        <p>Predicting changes from scaling advanced AI systems is a desirable property.</p>
      </div>
    </article>
  </body>
</html>
"""


def test_extractor_version_bumped_for_icml_style_fix():
    """#175 auto-invalidates old HTML caches after this extractor change."""
    assert EXTRACTOR_VERSION >= 4


def test_html_to_text_strips_icml_style_warnings_before_title():
    text = _html_to_text(ICML_STYLE_WARNING_HTML)
    assert text.lstrip().startswith(
        "Why Has Predicting Downstream Capabilities of Frontier AI Models"
    )
    assert "Predicting changes from scaling" in text
    leaked = [
        "marginparsep",
        "topmargin has been altered",
        "marginparpush",
        "page layout violates the ICML style",
        "Please do not change the page layout",
        "packages like geometry",
        "layout-changing commands",
        "reliably undo arbitrary changes",
    ]
    for snippet in leaked:
        assert snippet.lower() not in text.lower(), snippet


def test_html_to_text_keeps_177_permission_and_footnotemark_strips():
    """#177 permission/footnotemark chrome must stay gone alongside #190."""
    html = """
    <html><body>
      <article class="ltx_document">
        <p>Provided proper attribution is provided, Google hereby grants permission to reproduce the tables and figures in this paper solely for use in journalistic or scholarly works.</p>
        <h1 class="ltx_title">Attention Is All You Need</h1>
        <span class="ltx_personname">Noam Shazeer<span class="ltx_note ltx_role_footnotemark"><sup class="ltx_note_mark">1</sup><span class="ltx_note_type">footnotemark: </span></span></span>
        <p>The dominant sequence transduction models are based on attention.</p>
      </article>
    </body></html>
    """
    text = _html_to_text(html)
    assert text.lstrip().startswith("Attention Is All You Need")
    assert "The dominant sequence transduction models" in text
    assert "permission to reproduce" not in text.lower()
    assert "footnotemark" not in text.lower()
