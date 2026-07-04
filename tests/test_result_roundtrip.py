from conference_analyzer.models import AnalysisResult, Paper, Topic


def _result() -> AnalysisResult:
    papers = [
        Paper(
            paper_id="2024.acl-long.1",
            title="A",
            url="u1",
            pdf_url="p1",
            authors=["Ada Lovelace", "Alan Turing"],
            abstract="abs1",
            relevant=True,
            confidence=0.91,
            reason="core",
            topic_ids=[0, 1],
            duplicate_of=None,
        ),
        Paper(
            paper_id="ijcai-42",
            title="B",
            url="u2",
            pdf_url="p2",
            confidence=0.6,
            relevant=True,
            topic_ids=[1],
            duplicate_of="2024.acl-long.1",
        ),
    ]
    topics = [
        Topic(topic_id=0, name="T0", description="d0", findings=["x"], paper_ids=["2024.acl-long.1"]),
        Topic(topic_id=1, name="T1", paper_ids=["2024.acl-long.1", "ijcai-42"]),
    ]
    return AnalysisResult(
        theme="Agentic AI",
        event_url="https://example.org/acl-2024",
        scanned=10,
        papers=papers,
        relevant_papers=papers,
        topics=topics,
        duplicate_groups=1,
        min_confidence=0.7,
    )


def test_result_roundtrips_through_dict():
    original = _result()
    restored = AnalysisResult.from_dict(original.to_dict())

    assert restored.theme == "Agentic AI"
    assert restored.scanned == 10
    assert restored.min_confidence == 0.7
    assert restored.duplicate_groups == 1
    assert len(restored.relevant_papers) == 2
    assert len(restored.topics) == 2

    p0 = restored.relevant_papers[0]
    assert p0.topic_ids == [0, 1]
    assert p0.confidence == 0.91
    assert p0.authors == ["Ada Lovelace", "Alan Turing"]
    p1 = restored.relevant_papers[1]
    assert p1.duplicate_of == "2024.acl-long.1"


def test_year_property_parses_leading_year():
    assert Paper("2024.acl-long.1", "t", "u", "p").year == 2024
    assert Paper("ijcai-42", "t", "u", "p").year is None


def test_from_dict_ignores_unknown_fields():
    p = Paper.from_dict({"paper_id": "x", "title": "t", "url": "u", "pdf_url": "p", "bogus": 1})
    assert p.paper_id == "x" and p.title == "t"
