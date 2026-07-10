from conflens.sources import SOURCES, PSCCSource, make_source

# Two paper blocks in the shape returned by PSCC's make_table.php (single-quoted
# attributes, a nested hidden citation div, entity-encoded characters).
FRAGMENT = (
    "Papers returned: 2."
    "<div class='paper'><p class='title'>A Bayesian Approach &amp; Wind Farms</p>"
    "<p class='authors'>Jos&eacute; Pessanha, Victor Almeida, Albert Melo</p>"
    "<p class='year'>2024&nbsp;|&nbsp;<a href='repo/papers/2024/2024_965.pdf'>PDF</a>&nbsp;|&nbsp;"
    "<a href='#' onclick='show_citation(\"p_repo/papers/2024/2024_965.pdf\");return false;'>Citation</a></p>"
    "<div class='citation' id='p_repo/papers/2024/2024_965.pdf' style='display: none;'>"
    "Jos&eacute; Pessanha et al., A Bayesian Approach, PSCC 2024.</div></div>"
    "<div class='paper'><p class='title'>Synthetic Distribution Systems</p>"
    "<p class='authors'>Henrique Caetano</p>"
    "<p class='year'>2024&nbsp;|&nbsp;<a href='repo/papers/2024/2024_421.pdf'>PDF</a></p></div>"
)


def test_pscc_registered_with_all_keys():
    assert "pscc" in SOURCES
    entry = SOURCES["pscc"]
    for key in ("label", "base", "target", "base_label", "target_label"):
        assert entry.get(key), f"missing {key}"


def test_make_source_returns_pscc_adapter(tmp_path):
    src = make_source("pscc", base_url="https://pscc-central.epfl.ch", cache_dir=str(tmp_path))
    assert isinstance(src, PSCCSource)


def test_parse_papers(tmp_path):
    src = PSCCSource(cache_dir=str(tmp_path))
    papers = src.parse_papers(FRAGMENT, "2024")
    assert [p.paper_id for p in papers] == ["pscc-2024_965", "pscc-2024_421"]

    p0 = papers[0]
    assert p0.title == "A Bayesian Approach & Wind Farms"          # entity unescaped
    assert p0.authors == ["José Pessanha", "Victor Almeida", "Albert Melo"]
    assert p0.pdf_url == "https://pscc-central.epfl.ch/repo/papers/2024/2024_965.pdf"
    assert p0.url == p0.pdf_url                                     # title links to the PDF
    assert p0.abstract == ""                                       # no abstract on the site
    assert papers[1].authors == ["Henrique Caetano"]


def test_year_and_url_helpers(tmp_path):
    src = PSCCSource(cache_dir=str(tmp_path))
    assert src._year("2024") == "2024"
    assert src._year("23rd PSCC 2022") == "2022"
    assert src._api_url("2024") == (
        "https://pscc-central.epfl.ch/repo/make_table.php?authors=&year=2024&title="
    )
    # a full URL passes through resolve_url; a year maps to the human repo page
    assert src.resolve_url("2024") == "https://pscc-central.epfl.ch/papers-repo"
    full = "https://pscc-central.epfl.ch/repo/make_table.php?authors=&year=2022&title="
    assert src.resolve_url(full) == full


def test_parse_skips_untitled_and_dedupes(tmp_path):
    src = PSCCSource(cache_dir=str(tmp_path))
    dupe = FRAGMENT + (
        "<div class='paper'><p class='title'>A Bayesian Approach &amp; Wind Farms</p>"
        "<p class='year'><a href='repo/papers/2024/2024_965.pdf'>PDF</a></p></div>"
        "<div class='paper'><p class='authors'>No Title Here</p></div>"
    )
    papers = src.parse_papers(dupe, "2024")
    assert [p.paper_id for p in papers] == ["pscc-2024_965", "pscc-2024_421"]  # dup + untitled dropped
