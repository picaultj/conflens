import re

import pytest

from conference_analyzer import classifier, topics
from conference_analyzer.models import Paper
from conference_analyzer.pipeline import AnalysisCancelled


class FakeClient:
    """Minimal LLMClient stand-in that satisfies the structured() contract."""

    model = "fake"

    def __init__(self):
        self.calls = 0

    def structured(self, system, user, schema, max_tokens=8000, effort="medium"):
        self.calls += 1
        props = schema["properties"]
        if "results" in props:  # classification
            idxs = [int(x) for x in re.findall(r"\[(\d+)\] Title", user)]
            return {"results": [
                {"index": i, "relevant": i % 2 == 0, "confidence": 0.9, "reason": "r"}
                for i in idxs
            ]}
        if "topics" in props:  # topic discovery
            return {"topics": [{"name": "T0", "description": "d0"},
                               {"name": "T1", "description": "d1"}]}
        if "assignments" in props:  # topic assignment
            idxs = [int(x) for x in re.findall(r"\[(\d+)\]", user) if x.isdigit()]
            return {"assignments": [{"index": i, "topic_id": i % 2} for i in idxs]}
        if "findings" in props:  # per-topic summary
            return {"description": "desc", "findings": ["a", "b", "c"]}
        raise AssertionError("unexpected schema")


def _papers(n=6):
    return [
        Paper(paper_id=f"p{i}", title=f"T{i}", url="u", pdf_url="", abstract="abs")
        for i in range(n)
    ]


def test_classification_cache_hit_and_threshold(tmp_path):
    papers = _papers()
    fc = FakeClient()
    rel = classifier.classify_papers(
        fc, "Theme", papers, min_confidence=0.5, cache_dir=str(tmp_path), cache_sig="s"
    )
    assert [p.paper_id for p in rel] == ["p0", "p2", "p4"]
    first_calls = fc.calls
    assert first_calls > 0

    # Re-run: full cache hit → no new LLM calls.
    fc2 = FakeClient()
    rel2 = classifier.classify_papers(
        fc2, "Theme", _papers(), min_confidence=0.5, cache_dir=str(tmp_path), cache_sig="s"
    )
    assert fc2.calls == 0
    assert [p.paper_id for p in rel2] == ["p0", "p2", "p4"]

    # Higher threshold reuses cache (no calls) and filters everything out (conf 0.9<0.95? no).
    fc3 = FakeClient()
    rel3 = classifier.classify_papers(
        fc3, "Theme", _papers(), min_confidence=0.95, cache_dir=str(tmp_path), cache_sig="s"
    )
    assert fc3.calls == 0
    assert rel3 == []  # 0.9 < 0.95


def test_classification_new_theme_recomputes(tmp_path):
    fc = FakeClient()
    classifier.classify_papers(fc, "A", _papers(), cache_dir=str(tmp_path), cache_sig="s")
    fc2 = FakeClient()
    classifier.classify_papers(fc2, "B", _papers(), cache_dir=str(tmp_path), cache_sig="s")
    assert fc2.calls > 0  # different theme → cache miss


def test_classification_cancel():
    papers = _papers(50)
    n = {"c": 0}

    def cancel():
        n["c"] += 1
        if n["c"] >= 2:
            raise AnalysisCancelled()

    with pytest.raises(AnalysisCancelled):
        classifier.classify_papers(FakeClient(), "X", papers, cancel=cancel)


def test_topic_modelling_and_summary(tmp_path):
    papers = _papers(4)
    fc = FakeClient()
    tops = topics.model_topics_llm(fc, "Theme", papers, n_topics=2)
    assert sum(t.count for t in tops) == len(papers)
    assert all(p.topic_id is not None for p in papers)

    fc2 = FakeClient()
    topics.summarize_topics(fc2, "Theme", tops, papers, cache_dir=str(tmp_path), cache_sig="s")
    assert all(t.findings for t in tops)
    calls_after_first = fc2.calls

    # Re-run summary with same membership → cache hit.
    fc3 = FakeClient()
    topics.summarize_topics(fc3, "Theme", tops, papers, cache_dir=str(tmp_path), cache_sig="s")
    assert fc3.calls == 0
    assert calls_after_first > 0
