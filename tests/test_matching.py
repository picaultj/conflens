from conference_analyzer.app import AnalyzerUI
from conference_analyzer.models import Paper


def _p(title="Agentic Planning with Tools", abstract="A study of retrieval methods."):
    return Paper(paper_id="1", title=title, url="u", pdf_url="", abstract=abstract)


def test_empty_query_matches_all():
    assert AnalyzerUI._paper_matches(_p(), "") is True
    assert AnalyzerUI._paper_matches(_p(), "   ") is True


def test_phrase_with_spaces():
    assert AnalyzerUI._paper_matches(_p(), "retrieval methods") is True
    assert AnalyzerUI._paper_matches(_p(), "graph neural") is False


def test_comma_separated_is_and():
    assert AnalyzerUI._paper_matches(_p(), "planning, retrieval") is True
    assert AnalyzerUI._paper_matches(_p(), "planning, vision") is False


def test_case_insensitive():
    assert AnalyzerUI._paper_matches(_p(), "PLANNING") is True


def test_matches_title_or_abstract():
    p = _p(title="Vision Transformers", abstract="")
    assert AnalyzerUI._paper_matches(p, "vision") is True   # title only
    p2 = _p(title="X", abstract="deep reinforcement learning")
    assert AnalyzerUI._paper_matches(p2, "reinforcement") is True  # abstract only
