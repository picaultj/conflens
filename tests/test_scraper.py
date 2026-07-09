from conflens.scraper import AnthologyScraper

# Minified, unquoted-attribute HTML in the ACL Anthology's style.
LISTING = (
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2024.acl-long.0/>Front Matter</a></strong></span>'
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2024.acl-long.1/>'
    'Quantized Side <span class=acl-fixed-case>Tuning</span></a></strong></span>'
    '<span class=d-block><strong>'
    '<a class=align-middle href=/2024.acl-long.2/>Another &amp; Paper</a></strong></span>'
)

PAPER_PAGE = (
    '<meta content="Zhengxin Zhang" name=citation_author>'
    '<meta content="Dan Zhao" name=citation_author>'
    '<div class="card-body acl-abstract"><h5 class=card-title>Abstract</h5>'
    '<span>Finetuning large language models is effective.</span></div>'
)


def test_parse_listing_skips_frontmatter_and_cleans_titles(tmp_path):
    s = AnthologyScraper(cache_dir=str(tmp_path))
    papers = s.parse_listing(LISTING)
    ids = [p.paper_id for p in papers]
    assert ids == ["2024.acl-long.1", "2024.acl-long.2"]  # .0 front-matter dropped
    assert papers[0].title == "Quantized Side Tuning"       # nested span stripped
    assert papers[1].title == "Another & Paper"             # entity unescaped
    assert papers[0].pdf_url.endswith("/2024.acl-long.1.pdf")
    assert papers[0].url.endswith("/2024.acl-long.1/")


def test_parse_listing_can_include_frontmatter(tmp_path):
    s = AnthologyScraper(cache_dir=str(tmp_path))
    ids = [p.paper_id for p in s.parse_listing(LISTING, include_frontmatter=True)]
    assert "2024.acl-long.0" in ids


def test_parse_detail_extracts_abstract_and_authors():
    abstract, authors = AnthologyScraper.parse_detail(PAPER_PAGE)
    assert abstract == "Finetuning large language models is effective."
    assert authors == ["Zhengxin Zhang", "Dan Zhao"]
