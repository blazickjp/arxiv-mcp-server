"""HTML extract: join decorative title letter spans / subtitle colon (#258)."""

from arxiv_mcp_server.tools.download import EXTRACTOR_VERSION, _html_to_text

# DAOP (2501.10375): underlined acronym letters are one glyph per <span>.
DAOP_LETTER_TITLE_HTML = """
<html>
  <body>
    <article class="ltx_document">
      <h1 class="ltx_title ltx_title_document">DAOP: <span id="id1" class="ltx_text ltx_underline">D</span>ata-<span id="id2" class="ltx_text ltx_underline">A</span>ware <span id="id3" class="ltx_text ltx_underline">O</span>ffloading and Predictive <span id="id4" class="ltx_text ltx_underline">P</span>re-Calculation for Efficient MoE Inference
        <span class="ltx_pubnotes">
          <span class="ltx_pubnotes_content">
            <span class="ltx_pubnote ltx_role_thanks">
              <span class="ltx_note_name">Thanks: </span>
              Proceedings of the DATE Conference
            </span>
          </span>
        </span>
      </h1>
      <div class="ltx_authors">
        <span class="ltx_personname">Yujie Zhang</span>
      </div>
      <div class="ltx_abstract">
        <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
        <p>Mixture-of-Experts models face deployment challenges.</p>
      </div>
    </article>
  </body>
</html>
"""

# ExpertFlow (2410.17954): italic name span then ``:`` subtitle fragment.
EXPERTFLOW_TITLE_COLON_HTML = """
<html>
  <body>
    <article class="ltx_document">
      <h1 class="ltx_title ltx_title_document"><span id="id1" class="ltx_text ltx_font_italic">ExpertFlow</span>: Optimized Expert Activation and Token Allocation for Efficient Mixture-of-Experts Inference
        <span class="ltx_pubnotes">
          <span class="ltx_pubnote ltx_role_doi">
            <span class="ltx_note_name">DOI: </span>10.1145/example
          </span>
        </span>
      </h1>
      <div class="ltx_authors">
        <span class="ltx_personname">Xin He</span>
      </div>
      <div class="ltx_abstract">
        <h6 class="ltx_title ltx_title_abstract">Abstract.</h6>
        <p>Sparse Mixture of Experts models face inference challenges.</p>
      </div>
    </article>
  </body>
</html>
"""


def test_extractor_version_bumped_for_title_letter_join():
    """#175 auto-invalidates old DAOP/ExpertFlow caches after this change."""
    assert EXTRACTOR_VERSION >= 6


def test_html_to_text_joins_decorative_title_letter_spans():
    text = _html_to_text(DAOP_LETTER_TITLE_HTML)
    expected = (
        "DAOP: Data-Aware Offloading and Predictive "
        "Pre-Calculation for Efficient MoE Inference"
    )
    assert expected in text
    # Must not remain letter-per-line after conversion.
    assert "\nD\n" not in text
    assert "\nA\n" not in text
    assert "\nO\n" not in text
    assert "\nP\n" not in text
    assert "Proceedings of the DATE Conference" not in text
    assert "Abstract" in text
    assert "Mixture-of-Experts models face deployment challenges." in text


def test_html_to_text_keeps_subtitle_colon_on_title_line():
    text = _html_to_text(EXPERTFLOW_TITLE_COLON_HTML)
    expected = (
        "ExpertFlow: Optimized Expert Activation and Token Allocation "
        "for Efficient Mixture-of-Experts Inference"
    )
    assert expected in text
    assert "\n: " not in text
    assert text.splitlines()[0] == expected
    assert "10.1145/example" not in text
    assert "Abstract." in text
