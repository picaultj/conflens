"""Shared data structures used across the pipeline."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
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

    # Filled in by topic modelling — a paper may belong to more than one topic
    # (primary first).
    topic_ids: list[int] = field(default_factory=list)

    # Filled in by near-duplicate detection: the representative paper_id of the
    # duplicate group (None if this paper is unique or the group's representative).
    duplicate_of: Optional[str] = None

    @property
    def topic_id(self) -> Optional[int]:
        """Primary topic (first assigned), for backward-compatible access."""
        return self.topic_ids[0] if self.topic_ids else None

    @property
    def year(self) -> Optional[int]:
        """Best-effort publication year parsed from the paper id (e.g. 2024.acl…)."""
        m = re.match(r"(19|20)\d{2}", self.paper_id or "")
        return int(m.group(0)) if m else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Paper":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class Topic:
    """A discovered topic within the selected theme."""

    topic_id: int
    name: str
    description: str = ""
    findings: list[str] = field(default_factory=list)  # common findings across the topic's papers
    paper_ids: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.paper_ids)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Topic":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class AnalysisResult:
    """The full output of a run, ready to render."""

    theme: str
    event_url: str
    scanned: int = 0
    papers: list[Paper] = field(default_factory=list)        # all scanned
    relevant_papers: list[Paper] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    duplicate_groups: int = 0                                # near-duplicate clusters found
    min_confidence: float = 0.5                              # threshold applied at run time (display default)

    def to_dict(self) -> dict:
        return {
            "theme": self.theme,
            "event_url": self.event_url,
            "scanned": self.scanned,
            "relevant_count": len(self.relevant_papers),
            "duplicate_groups": self.duplicate_groups,
            "min_confidence": self.min_confidence,
            "topics": [t.to_dict() for t in self.topics],
            "papers": [p.to_dict() for p in self.relevant_papers],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisResult":
        """Rebuild a result from its ``to_dict`` form (for save/load of a run)."""
        relevant = [Paper.from_dict(p) for p in d.get("papers", [])]
        result = cls(
            theme=d.get("theme", ""),
            event_url=d.get("event_url", ""),
            scanned=int(d.get("scanned", 0)),
            papers=relevant,                 # all scanned isn't persisted; relevant is enough to render
            relevant_papers=relevant,
            topics=[Topic.from_dict(t) for t in d.get("topics", [])],
            duplicate_groups=int(d.get("duplicate_groups", 0)),
            min_confidence=float(d.get("min_confidence", 0.5)),
        )
        return result
