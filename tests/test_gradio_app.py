import pytest

pytest.importorskip("gradio")  # only runs when the `gradio` extra is installed

from conflens.models import AnalysisResult, Paper, Topic  # noqa: E402


def _result() -> AnalysisResult:
    papers = [
        Paper("p1", "Agentic Planning", "http://x/1", "http://x/1.pdf",
              ["Ada Lovelace"], "tool agents", True, 0.9, "core", [0, 1]),
        Paper("p2", "Graph Networks", "http://x/2", "http://x/2.pdf",
              ["Marie Curie"], "gnn", True, 0.6, "core", [1]),
    ]
    topics = [Topic(0, "Agents", "d", ["f"], ["p1"]), Topic(1, "Graphs", "d", ["f"], ["p1", "p2"])]
    return AnalysisResult("Agentic AI", "https://x", 50, papers, papers, topics, 0, 0.5)


def test_render_html_highlights_and_filters():
    from conflens import gradio_app as g

    html_, status = g._render_html(_result(), 0.5, "agent", "", "confidence", False)
    assert "<mark>" in html_                      # keyword highlighted
    assert "Papers per topic" in html_            # chart rendered
    assert "Agents" in html_                      # topic card
    assert "of 2 papers" in status

    # raising the threshold hides the 0.60 paper in global mode
    h2, s2 = g._render_html(_result(), 0.7, "", "", "title", True)
    assert "Graph Networks" not in h2
    assert "1 of 2 papers" in s2


def test_build_demo_constructs():
    from conflens import gradio_app as g

    demo = g.build_demo()
    assert demo.__class__.__name__ == "Blocks"


def test_summary_md():
    from conflens import gradio_app as g

    md = g._summary_md(_result())
    assert "50" in md and "Agentic AI" in md and "topics" in md
