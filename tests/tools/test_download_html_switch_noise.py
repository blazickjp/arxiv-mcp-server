"""HTML extract: Switch Transformers author/cite/figure noise (#260)."""

from arxiv_mcp_server.tools.download import EXTRACTOR_VERSION, _html_to_text

# Shaped like Switch Transformers (2101.03961): pre-title byline duplicates
# ``ltx_authors``, journal heading/shortheadings notes, and latexml splits
# citations / figure refs across text nodes.
SWITCH_NOISE_HTML = """
<html>
  <body>
    <article class="ltx_document ltx_authors_1line">
      <div id="p1" class="ltx_para">
        <p id="p1.1" class="ltx_p">William Fedus, Barret Zoph and Noam Shazeer</p>
      </div>
      <h1 class="ltx_title ltx_title_document">
        Switch Transformers: Scaling to Trillion Parameter Models
      </h1>
      <div class="ltx_authors">
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">William Fedus</span>
        </span>
        <span class="ltx_author_before">  </span>
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Barret Zoph<sup class="ltx_sup">*</sup></span>
        </span>
        <span class="ltx_author_before">  </span>
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Noam Shazeer</span>
          <span class="ltx_author_notes">
            <span class="ltx_contact ltx_role_affiliation">
              <span class="ltx_contact_name">Affiliation: </span>Google
            </span>
          </span>
        </span>
      </div>
      <span class="ltx_note ltx_role_heading">
        <sup class="ltx_note_mark">†</sup>
        <span class="ltx_note_type">heading: </span>23 2022 1- 8/21; Revised
      </span>
      <span class="ltx_note ltx_role_shortheadings">
        <sup class="ltx_note_mark">†</sup>
        <span class="ltx_note_type">shortheadings: </span>
        Switch Transformers / Fedus, Zoph and Shazeer
      </span>
      <span class="ltx_note ltx_role_editor">
        <sup class="ltx_note_mark">†</sup>
        <span class="ltx_note_type">editor: </span>Alexander Clark
      </span>
      <div class="ltx_abstract">
        <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
        <p>
          We design models based off T5-Base and T5-Large
          <cite class="ltx_cite ltx_citemacro_citep">
            (<a href="#bib.bib36" class="ltx_ref">Raffel et al. 2019</a>)
          </cite>
          and cite prior work
          <cite class="ltx_cite ltx_citemacro_citep">
            (<a href="#bib.bib1" class="ltx_ref">Radford et al. 2018</a>;
            <a href="#bib.bib2" class="ltx_ref">Kaplan et al. 2020</a>;
            <a href="#bib.bib3" class="ltx_ref">Brown et al. 2020</a>)
          </cite>.
          Bracket form
          <cite class="ltx_cite">[<a href="#bib.bib4" class="ltx_ref">1</a>]</cite>
          and figure reference Figure
          <a href="#S2.F3" class="ltx_ref"><span class="ltx_text ltx_ref_tag">3</span></a>
          shows routing. Also Fig.
          <a href="#S2.F2" class="ltx_ref"><span class="ltx_text ltx_ref_tag">2</span></a>
          for comparison.
        </p>
      </div>
    </article>
  </body>
</html>
"""


def test_extractor_version_bumped_for_switch_noise_cleanup():
    """#175 auto-invalidates Switch caches after this extractor change."""
    assert EXTRACTOR_VERSION >= 7


def test_html_to_text_dedupes_switch_author_byline():
    text = _html_to_text(SWITCH_NOISE_HTML)
    assert text.count("William Fedus") == 1
    assert text.count("Barret Zoph") == 1
    assert text.count("Noam Shazeer") == 1
    # Coherent single author line with commas between creators.
    assert "William Fedus, Barret Zoph, Noam Shazeer" in text
    assert "Affiliation:" not in text
    assert "Google" not in text


def test_html_to_text_drops_journal_heading_chrome():
    text = _html_to_text(SWITCH_NOISE_HTML)
    for leaked in [
        "Switch Transformers / Fedus, Zoph and Shazeer",
        "Alexander Clark",
        "8/21; Revised",
        "heading:",
        "shortheadings:",
        "editor:",
    ]:
        assert leaked not in text, leaked


def test_html_to_text_joins_split_citations_and_figure_refs():
    text = _html_to_text(SWITCH_NOISE_HTML)
    assert "(Raffel et al. 2019)" in text
    assert "(Radford et al. 2018; Kaplan et al. 2020; Brown et al. 2020)" in text
    assert "[1]" in text
    assert "\n1\n" not in text
    assert "[\n" not in text
    assert "Figure 3 shows" in text
    assert "Fig. 2 for" in text
    assert "\nFigure\n" not in text
    assert "\nFig.\n" not in text
    lines = text.splitlines()
    assert "Figure" not in lines
    assert "Fig." not in lines
