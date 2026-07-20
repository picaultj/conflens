from conflens import view
from conflens.models import Paper


def _p(title="Agentic Planning with Tools", abstract="A study of retrieval methods."):
    return Paper(paper_id="1", title=title, url="u", pdf_url="", abstract=abstract)


def _match(paper, query: str) -> bool:
    return view.matches(paper, view.keywords(query))


def test_empty_query_matches_all():
    assert _match(_p(), "") is True
    assert _match(_p(), "   ") is True


def test_phrase_with_spaces():
    assert _match(_p(), "retrieval methods") is True
    assert _match(_p(), "graph neural") is False


def test_comma_separated_is_and():
    assert _match(_p(), "planning, retrieval") is True
    assert _match(_p(), "planning, vision") is False


def test_case_insensitive():
    assert _match(_p(), "PLANNING") is True


def test_matches_title_or_abstract():
    p = _p(title="Vision Transformers", abstract="")
    assert _match(p, "vision") is True   # title only
    p2 = _p(title="X", abstract="deep reinforcement learning")
    assert _match(p2, "reinforcement") is True  # abstract only


def test_highlight_wraps_keywords_and_escapes():
    out = view.highlight("Vision & Transformers", ["vision"])
    assert "<mark>Vision</mark>" in out  # case-insensitive, original case kept
    assert "&amp;" in out                # HTML-escaped
    assert view.highlight("plain", []) == "plain"


def _sortable():
    return [
        Paper("2022.acl.1", "Beta", "u", "", confidence=0.4),
        Paper("2024.acl.1", "alpha", "u", "", confidence=0.9),
        Paper("ijcai-3", "Gamma", "u", "", confidence=0.6),  # no year
    ]


def test_sort_by_confidence_desc():
    assert [p.confidence for p in view.sort_papers(_sortable(), "confidence")] == [0.9, 0.6, 0.4]


def test_sort_by_title_casefold_asc():
    assert [p.title for p in view.sort_papers(_sortable(), "title")] == ["alpha", "Beta", "Gamma"]


def test_sort_by_year_desc_missing_last():
    assert [p.year for p in view.sort_papers(_sortable(), "year")] == [2024, 2022, None]
