from conflens.scraper import AnthologyScraper
from conflens.sources import SOURCES, make_source

# ACL-Anthology-style listing, but with NAACL paper ids.
LISTING = (
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2024.naacl-long.0/>Front Matter</a></strong></span>'
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2024.naacl-long.1/>Agentic Reasoning</a></strong></span>'
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2024.naacl-short.2/>Tools &amp; Memory</a></strong></span>'
)


def test_naacl_registered_with_all_keys():
    assert "naacl" in SOURCES
    entry = SOURCES["naacl"]
    for key in ("label", "base", "target", "base_label", "target_label"):
        assert entry.get(key), f"missing {key}"
    assert entry["target"].startswith("naacl-")


def test_make_source_naacl_uses_anthology_adapter(tmp_path):
    src = make_source("naacl", base_url="https://aclanthology.org", cache_dir=str(tmp_path))
    assert isinstance(src, AnthologyScraper)


def test_naacl_listing_parses_naacl_ids(tmp_path):
    src = make_source("naacl", base_url="https://aclanthology.org", cache_dir=str(tmp_path))
    papers = src.parse_listing(LISTING)
    ids = [p.paper_id for p in papers]
    assert ids == ["2024.naacl-long.1", "2024.naacl-short.2"]  # front-matter dropped
    assert papers[0].title == "Agentic Reasoning"
    assert papers[1].title == "Tools & Memory"
    assert papers[0].pdf_url.endswith("/2024.naacl-long.1.pdf")


def test_naacl_event_url_slug_and_passthrough(tmp_path):
    src = make_source("naacl", base_url="https://aclanthology.org", cache_dir=str(tmp_path))
    assert src.resolve_url("naacl-2024") == "https://aclanthology.org/events/naacl-2024/"
    full = "https://aclanthology.org/events/naacl-2022/"
    assert src.resolve_url(full) == full
