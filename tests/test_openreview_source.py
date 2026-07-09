from conflens.sources import OpenReviewSource, _cv

# API v2 note: content values are wrapped in {"value": …}; PDF is a relative path.
NOTE_V2 = {
    "id": "abc123",
    "forum": "abc123",
    "content": {
        "title": {"value": "Scaling Agentic Reasoning"},
        "authors": {"value": ["Ada Lovelace", "Alan Turing"]},
        "abstract": {"value": "We study tool-using agents at scale."},
        "keywords": {"value": ["agents", "reasoning"]},
        "pdf": {"value": "/pdf?id=abc123"},
        "venueid": {"value": "ICLR.cc/2024/Conference"},
    },
}
# API v1 note: bare content values; no explicit pdf field.
NOTE_V1 = {
    "id": "def456",
    "content": {
        "title": "Graph Memory for Agents",
        "authors": ["Grace Hopper"],
        "abstract": "A study of memory.",
    },
}


def test_cv_handles_both_api_shapes():
    assert _cv({"t": {"value": 5}}, "t") == 5     # v2 wrapped
    assert _cv({"t": 5}, "t") == 5                # v1 bare
    assert _cv({}, "t", "d") == "d"              # missing → default


def test_parse_notes_v2_and_v1(tmp_path):
    src = OpenReviewSource(cache_dir=str(tmp_path))
    papers = src.parse_notes([NOTE_V2, NOTE_V1])
    assert [p.paper_id for p in papers] == ["openreview-abc123", "openreview-def456"]

    p0 = papers[0]
    assert p0.title == "Scaling Agentic Reasoning"
    assert p0.authors == ["Ada Lovelace", "Alan Turing"]
    assert p0.url == "https://openreview.net/forum?id=abc123"
    assert p0.pdf_url == "https://openreview.net/pdf?id=abc123"  # relative pdf → absolute
    assert "tool-using agents" in p0.abstract
    assert "Keywords: agents; reasoning" in p0.abstract

    p1 = papers[1]
    assert p1.title == "Graph Memory for Agents"
    assert p1.pdf_url == "https://openreview.net/pdf?id=def456"  # synthesised from id
    assert "Keywords:" not in p1.abstract                        # no keywords present


def test_parse_notes_skips_untitled_and_dedupes(tmp_path):
    src = OpenReviewSource(cache_dir=str(tmp_path))
    dup = dict(NOTE_V2)
    papers = src.parse_notes([NOTE_V2, {"id": "x", "content": {}}, dup])
    assert [p.paper_id for p in papers] == ["openreview-abc123"]  # untitled dropped, dup collapsed


def test_venue_id_extraction():
    assert OpenReviewSource._venue_id("ICLR.cc/2024/Conference") == "ICLR.cc/2024/Conference"
    assert (
        OpenReviewSource._venue_id("https://openreview.net/group?id=NeurIPS.cc/2024/Conference")
        == "NeurIPS.cc/2024/Conference"
    )
    assert OpenReviewSource._venue_id("ICLR.cc/2024/Conference/") == "ICLR.cc/2024/Conference"


def test_resolve_url_builds_group_page(tmp_path):
    src = OpenReviewSource(cache_dir=str(tmp_path))
    assert (
        src.resolve_url("ICLR.cc/2024/Conference")
        == "https://openreview.net/group?id=ICLR.cc/2024/Conference"
    )


def test_api_roots_fallback(tmp_path):
    src = OpenReviewSource(cache_dir=str(tmp_path))  # default api2
    roots = src._api_roots()
    assert roots[0] == "https://api2.openreview.net"
    assert "https://api.openreview.net" in roots      # v1 fallback present
