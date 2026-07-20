from conflens import view
from conflens.models import AnalysisResult, Paper, Topic


def _result() -> AnalysisResult:
    papers = [
        Paper("p1", "Agentic Planning", "u1", "pdf1", ["Ada Lovelace"], "tool agents", True, 0.9, "r", [0, 1]),
        Paper("p2", "Graph Networks", "u2", "pdf2", ["Marie Curie"], "gnn", True, 0.6, "r", [1]),
        Paper("p3", "Low Conf", "u3", "pdf3", ["Ada Lovelace"], "meh", True, 0.3, "r", [0]),
    ]
    papers[2].duplicate_of = "p1"
    topics = [
        Topic(0, "Agents", "d", ["f"], ["p1", "p3"]),
        Topic(1, "Graphs", "d", ["f"], ["p1", "p2"]),
    ]
    return AnalysisResult("Agentic AI", "https://x", 50, papers, papers, topics, 1, 0.5)


def test_compute_view_threshold_and_counts():
    vd = view.compute_view(_result(), min_conf=0.5)
    # p3 (0.30) is filtered out; p1 in both topics, p2 in Graphs
    assert vd.names == ["Agents", "Graphs"]
    assert vd.counts == [1, 2]                 # Agents: p1 ; Graphs: p1,p2
    assert {tv.topic.name for tv in vd.grouped} == {"Agents", "Graphs"}


def test_compute_view_global_flat_dedupes_multitopic():
    vd = view.compute_view(_result(), min_conf=0.5, sort="title")
    ids = [p.paper_id for p in vd.flat]
    assert ids == ["p1", "p2"]                 # p1 appears once despite two topics; p3 below threshold


def test_compute_view_author_filter():
    vd = view.compute_view(_result(), min_conf=0.0, author="Marie Curie")
    assert [p.paper_id for p in vd.flat] == ["p2"]


def test_compute_view_keyword_filter():
    vd = view.compute_view(_result(), min_conf=0.0, query="graph")
    assert [p.paper_id for p in vd.flat] == ["p2"]


def test_author_choices_sorted_unique():
    assert view.author_choices(_result()) == ["Ada Lovelace", "Marie Curie"]


def test_dup_title_and_also_in():
    r = _result()
    all_by_id = {p.paper_id: p for p in r.papers}
    topic_name = {t.topic_id: t.name for t in r.topics}
    assert view.dup_title(all_by_id["p3"], all_by_id) == "Agentic Planning"
    assert view.also_in(all_by_id["p1"], 0, topic_name) == ["Graphs"]


def test_csv_and_json_bytes_roundtrip():
    r = _result()
    csv_text = view.csv_bytes(r).decode()
    assert "paper_id" in csv_text and "Agentic Planning" in csv_text and "duplicate_of" in csv_text
    restored = AnalysisResult.from_dict(__import__("json").loads(view.json_bytes(r)))
    assert len(restored.relevant_papers) == 3 and restored.duplicate_groups == 1
