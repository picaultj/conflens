from conflens.bibtex import build_bibtex
from conflens.cache import clear_cache
from conflens.models import AnalysisResult, Paper


def test_build_bibtex_entries_and_keys():
    papers = [
        Paper(paper_id="2024.acl-long.1", title="Quantized {Tuning}",
              url="https://x/1/", pdf_url="p", authors=["Ann Lee", "Bo Ng"], relevant=True),
        Paper(paper_id="ijcai-2026-29", title="Frequency Learning",
              url="https://y/", pdf_url="", authors=["Yu Liu"], relevant=True),
    ]
    r = AnalysisResult(theme="Agentic AI", event_url="e", scanned=2, relevant_papers=papers)
    bib = build_bibtex(r)
    assert "@inproceedings{p2024acllong1," in bib   # sanitized, leading digit → p-prefixed
    assert "@inproceedings{ijcai202629," in bib
    assert "author = {Ann Lee and Bo Ng}" in bib
    assert "year = {2024}" in bib
    assert "year = {2026}" in bib
    assert "{Tuning}".strip("{}") in bib            # braces stripped from title content
    assert bib.startswith("% 2 papers")


def test_build_bibtex_empty():
    r = AnalysisResult(theme="X", event_url="e", scanned=0, relevant_papers=[])
    assert build_bibtex(r).startswith("% 0 papers")


def test_clear_cache(tmp_path):
    (tmp_path / "listing_a.json").write_text("{}")
    (tmp_path / "classify_b.json").write_text("{}")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "x.json").write_text("{}")

    target, removed = clear_cache(str(tmp_path))
    assert target == str(tmp_path)
    assert removed == 3
    assert list(tmp_path.iterdir()) == []

    # Idempotent on an empty dir.
    _, removed2 = clear_cache(str(tmp_path))
    assert removed2 == 0
