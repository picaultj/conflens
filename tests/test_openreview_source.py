from conflens.sources import OpenReviewSource, _cv, auth_fields, make_source

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


# -- auth wiring (GUI-supplied credentials) --------------------------------- #
def test_auth_token_from_constructor_sets_bearer(tmp_path):
    src = OpenReviewSource(cache_dir=str(tmp_path), auth={"OPENREVIEW_TOKEN": "tok-123"})
    assert src._headers()["Authorization"] == "Bearer tok-123"


def test_no_auth_no_env_is_anonymous(tmp_path, monkeypatch):
    for v in ("OPENREVIEW_TOKEN", "OPENREVIEW_USERNAME", "OPENREVIEW_EMAIL", "OPENREVIEW_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    src = OpenReviewSource(cache_dir=str(tmp_path))
    assert "Authorization" not in src._headers()


def test_constructor_auth_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENREVIEW_TOKEN", "env-tok")
    src = OpenReviewSource(cache_dir=str(tmp_path), auth={"OPENREVIEW_TOKEN": "gui-tok"})
    assert src._headers()["Authorization"] == "Bearer gui-tok"  # GUI value wins over env


def test_empty_auth_values_ignored_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENREVIEW_TOKEN", "env-tok")
    src = OpenReviewSource(cache_dir=str(tmp_path), auth={"OPENREVIEW_TOKEN": "  "})
    assert src._headers()["Authorization"] == "Bearer env-tok"  # blank field → env fallback


def test_auth_fields_only_for_openreview():
    assert [f["env"] for f in auth_fields("openreview")] == [
        "OPENREVIEW_TOKEN", "OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"
    ]
    # Public sources declare no credential fields → nothing shown in the GUI.
    assert auth_fields("aclanthology") == []
    assert auth_fields("emnlp") == []
    assert auth_fields("ijcai") == []
    assert auth_fields("pscc") == []


def test_make_source_threads_auth_to_openreview(tmp_path):
    src = make_source(
        "openreview", "https://api2.openreview.net",
        cache_dir=str(tmp_path), auth={"OPENREVIEW_TOKEN": "mk-tok"},
    )
    assert isinstance(src, OpenReviewSource)
    assert src._headers()["Authorization"] == "Bearer mk-tok"
