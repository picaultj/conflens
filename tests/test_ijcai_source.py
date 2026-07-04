from conference_analyzer.sources import IJCAISource

# Two IJCAI items: one main-track (#29), one special-track (#AI4G6).
PAGE = (
    '<li class="ij-paper" data-search="...">'
    '<div class="ij-pid">#29</div>'
    '<h3 class="ij-ptitle">Frequency-Aware Learning</h3>'
    '<div class="ij-authors"><span class="ij-author">Yusen Liu</span>'
    '<span class="ij-sep">, </span><span class="ij-author">Hua Lu</span></div>'
    '<div class="ij-abstract">We study contrastive learning of time series.</div>'
    '<div class="ij-keywords"><span class="ij-kw" title="Data Mining → Temporal">'
    '<span class="ij-kw-area">Data Mining</span>Temporal</span></div>'
    '</li>'
    '<li class="ij-paper" data-search="...">'
    '<div class="ij-pid">#AI4G6</div>'
    '<h3 class="ij-ptitle">PhyTTA</h3>'
    '<div class="ij-authors"><span class="ij-author">Wentao Gao</span></div>'
    '<div class="ij-abstract">Physics-informed adaptation.</div>'
    '</li>'
)


def test_parse_papers_main_and_special_tracks(tmp_path):
    src = IJCAISource(base_url="https://2026.ijcai.org", cache_dir=str(tmp_path))
    papers = src.parse_papers(PAGE, "https://2026.ijcai.org/accepted-papers/")
    assert [p.paper_id for p in papers] == ["ijcai-2026-29", "ijcai-2026-AI4G6"]
    first = papers[0]
    assert first.title == "Frequency-Aware Learning"
    assert first.authors == ["Yusen Liu", "Hua Lu"]
    assert first.pdf_url == ""                              # no PDFs for accepted papers
    assert "contrastive learning" in first.abstract
    assert "Keywords:" in first.abstract                    # keywords appended
    assert papers[1].paper_id == "ijcai-2026-AI4G6"         # alphanumeric ID kept


def test_resolve_url_passthrough_and_slug(tmp_path):
    src = IJCAISource(base_url="https://2026.ijcai.org/", cache_dir=str(tmp_path))
    assert src.resolve_url("accepted-papers") == "https://2026.ijcai.org/accepted-papers/"
    full = "https://2026.ijcai.org/accepted-papers/"
    assert src.resolve_url(full) == full
