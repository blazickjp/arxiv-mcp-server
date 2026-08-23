"""HTML extract: author/CCS front-matter and \\times cleanup (#239)."""

from arxiv_mcp_server.tools.download import EXTRACTOR_VERSION, _html_to_text

# Representative latexml snippet shaped like ExpertFlow (2410.17954):
# pubnotes in the title, affiliation superscripts, and bare \\times math.
EXPERTFLOW_FRONTMATTER_HTML = """
<html>
  <body>
    <article class="ltx_document ltx_authors_1line">
      <h1 class="ltx_title ltx_title_document">
        <span class="ltx_text ltx_font_italic">ExpertFlow</span>: Efficient MoE Inference
        <span class="ltx_pubnotes">
          <span class="ltx_pubnotes_content">
            <span class="ltx_pubnote ltx_role_conference">
              <span class="ltx_note_name">Conference: </span>
              63rd ACM/IEEE Design Automation Conference; July 26–29, 2026; Long Beach, CA, USA
            </span>
            <span class="ltx_pubnote ltx_role_booktitle">
              63rd ACM/IEEE Design Automation Conference (DAC ’26)
            </span>
            <span class="ltx_pubnote ltx_role_doi">
              <span class="ltx_note_name">DOI: </span>
              <a href="https://doi.org/10.1145/3770743.3804292">10.1145/3770743.3804292</a>
            </span>
            <span class="ltx_pubnote ltx_role_isbn">
              <span class="ltx_note_name">ISBN: </span>979-8-4007-2254-7/2026/07
            </span>
            <span class="ltx_pubnote ltx_role_submissionid">2199</span>
            <span class="ltx_pubnote ltx_role_ccs">
              <span class="ltx_note_name">CCS: </span>
              Computer systems organization Heterogeneous (hybrid) systems
            </span>
            <span class="ltx_pubnote ltx_role_ccs">
              <span class="ltx_note_name">CCS: </span>
              Computing methodologies Neural networks
            </span>
          </span>
        </span>
      </h1>
      <div class="ltx_authors">
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Xin He
            <math class="ltx_Math" alttext="{}^{1_{\\textsuperscript{*}}}" display="inline">
              <semantics>
                <msup><mrow></mrow><mn>1</mn></msup>
                <annotation encoding="application/x-tex">{}^{1_{\\textsuperscript{*}}}</annotation>
              </semantics>
            </math>,
            Shunkang Zhang<sup class="ltx_sup"><span class="ltx_text">2</span></sup>,
            Kaijie Tang<sup class="ltx_sup"><span class="ltx_text">3</span></sup>,
            Yew Soon Ong<sup class="ltx_sup"><span class="ltx_text">1,5</span></sup>
          </span>
          <span class="ltx_author_notes">
            <span class="ltx_contact ltx_role_affiliation">
              <span class="ltx_contact_name">Affiliation: </span>
              <sup class="ltx_sup">1</sup>CFAR, A*STAR
            </span>
          </span>
        </span>
      </div>
      <div class="ltx_dates">2026; © cc</div>
      <span class="ltx_note ltx_role_cc-license">
        <sup class="ltx_note_mark">†</sup>
        <span class="ltx_note_type">cc-license: </span>by-nc-nd
      </span>
      <div class="ltx_abstract">
        <h6 class="ltx_title ltx_title_abstract">Abstract.</h6>
        <p>Improving inference throughput by up to 10
          <math class="ltx_Math" alttext="\\times" display="inline">
            <semantics>
              <mo>×</mo>
              <annotation encoding="application/x-tex">\\times</annotation>
            </semantics>
          </math>
          over strong offloading baselines.
        </p>
      </div>
    </article>
  </body>
</html>
"""


def test_extractor_version_bumped_for_frontmatter_cleanup():
    """#175 auto-invalidates old ExpertFlow caches after this extractor change."""
    assert EXTRACTOR_VERSION >= 5


def test_html_to_text_coalesces_authors_and_strips_frontmatter():
    text = _html_to_text(EXPERTFLOW_FRONTMATTER_HTML)
    # Coherent author line — not one token / affiliation mark per line.
    assert "Xin He, Shunkang Zhang, Kaijie Tang, Yew Soon Ong" in text
    assert "Xin He\n" not in text
    assert "textsuperscript" not in text
    assert "{}^{" not in text

    # Front-matter garbage before Abstract must be gone.
    leaked = [
        "CCS:",
        "DOI:",
        "ISBN:",
        "Conference:",
        "Design Automation Conference",
        "979-8-4007-2254-7",
        "10.1145/3770743.3804292",
        "2199",
        "by-nc-nd",
        "2026; © cc",
        "Affiliation:",
        "CFAR, A*STAR",
    ]
    for snippet in leaked:
        assert snippet not in text, snippet

    assert "Abstract." in text
    assert "Improving inference throughput" in text


def test_html_to_text_normalizes_times_math_noise():
    text = _html_to_text(EXPERTFLOW_FRONTMATTER_HTML)
    assert "10× over" in text
    assert "\\times" not in text
    assert "\n×\n" not in text
