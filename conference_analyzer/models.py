"""Shared data structures used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Paper:
    """A single paper scraped from the anthology."""

    paper_id: str          # e.g. "2024.acl-long.1"
    title: str
    url: str               # canonical landing page
    pdf_url: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""

    # Filled in by the classifier
    relevant: Optional[bool] = None
    confidence: Optional[float] = None
    reason: str = ""

    # Filled in by topic modelling
    topic_id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Topic:
    """A discovered topic within the selected theme."""

    topic_id: int
    name: str
    description: str = ""
    paper_ids: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.paper_ids)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisResult:
    """The full output of a run, ready to render."""

    theme: str
    event_url: str
    scanned: int = 0
    papers: list[Paper] = field(default_factory=list)        # all scanned
    relevant_papers: list[Paper] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "theme": self.theme,
            "event_url": self.event_url,
            "scanned": self.scanned,
            "relevant_count": len(self.relevant_papers),
            "topics": [t.to_dict() for t in self.topics],
            "papers": [p.to_dict() for p in self.relevant_papers],
        }
