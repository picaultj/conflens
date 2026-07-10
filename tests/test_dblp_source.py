from conflens.sources import SOURCES, DBLPSource, make_source

# Two hits in the shape of DBLP's publ search API: authors as a list and as a
# single dict, disambiguation suffixes, a trailing period on the title.
HITS = [
    {
        "info": {
            "title": "Safety and Security Dependencies for Gridshield.",
            "authors": {"author": [
                {"@pid": "195/3956-1", "text": "Reza Soltani 0001"},
                {"@pid": "191/1849", "text": "Baver Ozceylan"},
            ]},
            "year": "2024",
            "key": "conf/isgteurope/0001OLKH24",
            "ee": "https://doi.org/10.1109/ISGTEUROPE62998.2024.10863084",
            "url": "https://dblp.org/rec/conf/isgteurope/0001OLKH24",
        }
    },
    {
        "info": {
            "title": "A Single-Author Paper",
            "authors": {"author": {"@pid": "1/2", "text": "Ada Lovelace"}},
            "year": "2024",
            "key": "conf/isgteurope/Lovelace24",
            "ee": ["https://doi.org/10.1109/X", "https://example.org/alt"],
        }
    },
]


def test_isgteurope_registered_and_uses_dblp(tmp_path):
    assert "isgteurope" in SOURCES
    for key in ("label", "base", "target", "base_label", "target_label"):
        assert SOURCES["isgteurope"].get(key), f"missing {key}"
    src = make_source("isgteurope", base_url="https://dblp.org", cache_dir=str(tmp_path))
    assert isinstance(src, DBLPSource)


def test_proc_key_normalisation():
    f = DBLPSource._proc_key
    assert f("isgteurope 2024") == "conf/isgteurope/isgteurope2024"
    assert f("conf/isgteurope/isgteurope2024") == "conf/isgteurope/isgteurope2024"
    assert f("https://dblp.org/db/conf/isgteurope/isgteurope2023.html") == \
        "conf/isgteurope/isgteurope2023"


def test_resolve_url(tmp_path):
    src = DBLPSource(cache_dir=str(tmp_path))
    assert src.resolve_url("isgteurope 2024") == \
        "https://dblp.org/db/conf/isgteurope/isgteurope2024.html"


def test_parse_hits(tmp_path):
    src = DBLPSource(cache_dir=str(tmp_path))
    papers = src.parse_hits(HITS)
    assert [p.paper_id for p in papers] == [
        "dblp-conf-isgteurope-0001OLKH24",
        "dblp-conf-isgteurope-Lovelace24",
    ]
    p0 = papers[0]
    assert p0.title == "Safety and Security Dependencies for Gridshield"   # trailing dot dropped
    assert p0.authors == ["Reza Soltani", "Baver Ozceylan"]                # disambiguation stripped
    assert p0.url == "https://doi.org/10.1109/ISGTEUROPE62998.2024.10863084"
    assert p0.pdf_url == "" and p0.abstract == ""                          # title-based

    p1 = papers[1]
    assert p1.authors == ["Ada Lovelace"]                                  # single-author dict
    assert p1.url == "https://doi.org/10.1109/X"                           # first ee link


def test_parse_hits_skips_untitled_and_dedupes(tmp_path):
    src = DBLPSource(cache_dir=str(tmp_path))
    extra = HITS + [
        {"info": {"key": "conf/isgteurope/0001OLKH24", "title": "dup."}},   # duplicate key
        {"info": {"key": "conf/x/y", "authors": {}}},                       # no title
    ]
    papers = src.parse_hits(extra)
    assert [p.paper_id for p in papers] == [
        "dblp-conf-isgteurope-0001OLKH24",
        "dblp-conf-isgteurope-Lovelace24",
    ]
