from conflens.scraper import AnthologyScraper
from conflens.sources import SOURCES, make_source

# ACL-Anthology-style listing, but with EMNLP paper ids.
LISTING = (
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2023.emnlp-main.0/>Front Matter</a></strong></span>'
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2023.emnlp-main.1/>Agentic Tool Use</a></strong></span>'
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2023.emnlp-findings.2/>Retrieval &amp; Memory</a></strong></span>'
)


def test_emnlp_registered_with_all_keys():
    assert "emnlp" in SOURCES
    entry = SOURCES["emnlp"]
    for key in ("label", "base", "target", "base_label", "target_label"):
        assert entry.get(key), f"missing {key}"
    assert entry["target"].startswith("emnlp-")


def test_make_source_emnlp_uses_anthology_adapter(tmp_path):
    src = make_source("emnlp", base_url="https://aclanthology.org", cache_dir=str(tmp_path))
    assert isinstance(src, AnthologyScraper)


def test_emnlp_listing_parses_emnlp_ids(tmp_path):
    src = make_source("emnlp", base_url="https://aclanthology.org", cache_dir=str(tmp_path))
    papers = src.parse_listing(LISTING)
    ids = [p.paper_id for p in papers]
    assert ids == ["2023.emnlp-main.1", "2023.emnlp-findings.2"]  # front-matter dropped
    assert papers[0].title == "Agentic Tool Use"
    assert papers[1].title == "Retrieval & Memory"
    assert papers[0].pdf_url.endswith("/2023.emnlp-main.1.pdf")


def test_emnlp_event_url_slug_and_passthrough(tmp_path):
    src = make_source("emnlp", base_url="https://aclanthology.org", cache_dir=str(tmp_path))
    assert src.resolve_url("emnlp-2024") == "https://aclanthology.org/events/emnlp-2024/"
    full = "https://aclanthology.org/events/emnlp-2023/"
    assert src.resolve_url(full) == full
