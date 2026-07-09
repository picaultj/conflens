from conflens.dedup import _normalise, annotate_duplicates
from conflens.models import Paper


def _p(pid, title):
    return Paper(paper_id=pid, title=title, url="u", pdf_url="", abstract="")


def test_normalise_strips_punctuation_and_case():
    assert _normalise("Deep Learning: A Study (v2)") == "deep learning a study v2"


def test_exact_normalised_duplicates_grouped():
    papers = [
        _p("a", "Retrieval-Augmented Generation"),
        _p("b", "Retrieval Augmented Generation"),   # punctuation-only diff → same
        _p("c", "A Completely Different Paper"),
    ]
    groups = annotate_duplicates(papers)
    assert groups == 1
    assert papers[0].duplicate_of is None            # representative
    assert papers[1].duplicate_of == "a"             # points at representative
    assert papers[2].duplicate_of is None            # unique


def test_fuzzy_near_duplicate():
    papers = [
        _p("a", "Efficient Transformers for Long Documents"),
        _p("b", "Efficient Transformers for Long Document"),  # minor wording
        _p("c", "Graph Neural Networks for Molecules"),
    ]
    groups = annotate_duplicates(papers)
    assert groups == 1
    assert papers[1].duplicate_of == "a"
    assert papers[2].duplicate_of is None


def test_no_duplicates():
    papers = [_p("a", "One"), _p("b", "Two"), _p("c", "Three")]
    assert annotate_duplicates(papers) == 0
    assert all(p.duplicate_of is None for p in papers)
