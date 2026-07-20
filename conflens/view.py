"""UI-agnostic view logic shared by the NiceGUI and Gradio front-ends.

Both GUIs render the same analysis, so the pure "how to filter / sort / highlight
/ serialise" logic lives here and is imported by each front-end. Keep this module
free of any GUI framework (no ``nicegui``, no ``gradio``) so both can use it.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .models import AnalysisResult, Paper, Topic

# Shared topic palette (used for chart bars / topic dots in both GUIs).
TOPIC_COLORS = [
    "#1f4e79", "#2b6cb0", "#3182ce", "#0b7285", "#2f855a",
    "#975a16", "#9b2c2c", "#6b46c1", "#b83280", "#4a5568",
]


# --------------------------------------------------------------------------- #
# Filtering / sorting / highlighting
# --------------------------------------------------------------------------- #
def keywords(query: str) -> list[str]:
    """Split a search query into keywords (comma-separated; may contain spaces)."""
    return [k.strip().lower() for k in (query or "").split(",") if k.strip()]


def matches(paper: Paper, kws: list[str]) -> bool:
    """True if the paper's title/abstract contains every keyword (AND)."""
    if not kws:
        return True
    text = f"{paper.title}\n{paper.abstract or ''}".lower()
    return all(k in text for k in kws)


def highlight(text: str, kws: list[str]) -> str:
    """HTML-escape ``text`` and wrap keyword occurrences in ``<mark>``."""
    esc = html.escape(text or "")
    if not kws:
        return esc
    pattern = re.compile("|".join(re.escape(k) for k in kws), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", esc)


def sort_papers(papers: list[Paper], sort: str) -> list[Paper]:
    """Sort by confidence (desc), title (asc) or year (desc, missing last)."""
    if sort == "title":
        return sorted(papers, key=lambda p: (p.title or "").casefold())
    if sort == "year":
        return sorted(papers, key=lambda p: (p.year or 0), reverse=True)
    return sorted(papers, key=lambda p: (p.confidence or 0), reverse=True)


def author_choices(result: AnalysisResult) -> list[str]:
    """Sorted unique author names across the relevant papers."""
    return sorted(
        {a for p in result.relevant_papers for a in p.authors if a},
        key=str.casefold,
    )


def also_in(paper: Paper, current_topic_id: Optional[int], topic_name: dict) -> list[str]:
    """Names of the paper's other topics (excluding ``current_topic_id``)."""
    return [
        topic_name[tid]
        for tid in paper.topic_ids
        if tid != current_topic_id and tid in topic_name
    ]


def dup_title(paper: Paper, all_by_id: dict) -> Optional[str]:
    """Title of the representative paper this one is a near-duplicate of, if any."""
    if paper.duplicate_of and paper.duplicate_of in all_by_id:
        return all_by_id[paper.duplicate_of].title
    return None


# --------------------------------------------------------------------------- #
# Computed view (drives the chart + the grouped / global paper lists)
# --------------------------------------------------------------------------- #
@dataclass
class TopicView:
    topic: Topic
    papers: list[Paper]


@dataclass
class ViewData:
    names: list[str] = field(default_factory=list)          # all topic names (chart order)
    counts: list[int] = field(default_factory=list)         # filtered paper count per topic
    grouped: list[TopicView] = field(default_factory=list)  # topics with >=1 match
    flat: list[Paper] = field(default_factory=list)         # unique matches (global mode)
    topic_name: dict = field(default_factory=dict)
    all_by_id: dict = field(default_factory=dict)
    total_relevant: int = 0
    total_topics: int = 0

    @property
    def shown_grouped_papers(self) -> int:
        return sum(len(tv.papers) for tv in self.grouped)


def compute_view(
    result: AnalysisResult,
    *,
    min_conf: float = 0.0,
    query: str = "",
    author: str = "",
    sort: str = "confidence",
) -> ViewData:
    """Apply the live filters and return everything both GUIs need to render."""
    kws = keywords(query)
    by_id = {p.paper_id: p for p in result.relevant_papers}
    all_by_id = {p.paper_id: p for p in result.papers}
    topic_name = {t.topic_id: t.name for t in result.topics}

    def passes(p: Paper) -> bool:
        if (p.confidence or 0) < min_conf:
            return False
        if author and author not in p.authors:
            return False
        return matches(p, kws)

    per_topic = {
        t.topic_id: sort_papers(
            [by_id[pid] for pid in t.paper_ids if pid in by_id and passes(by_id[pid])],
            sort,
        )
        for t in result.topics
    }
    names = [t.name for t in result.topics]
    counts = [len(per_topic[t.topic_id]) for t in result.topics]
    grouped = [TopicView(t, per_topic[t.topic_id]) for t in result.topics if per_topic[t.topic_id]]

    seen: dict = {}
    for t in result.topics:
        for p in per_topic[t.topic_id]:
            seen.setdefault(p.paper_id, p)
    flat = sort_papers(list(seen.values()), sort)

    return ViewData(
        names=names,
        counts=counts,
        grouped=grouped,
        flat=flat,
        topic_name=topic_name,
        all_by_id=all_by_id,
        total_relevant=len(result.relevant_papers),
        total_topics=len(result.topics),
    )


# --------------------------------------------------------------------------- #
# Exports (bytes, so any GUI can offer them as downloads)
# --------------------------------------------------------------------------- #
def json_bytes(result: AnalysisResult) -> bytes:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")


def csv_bytes(result: AnalysisResult) -> bytes:
    topics = {t.topic_id: t.name for t in result.topics}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["paper_id", "title", "topics", "confidence", "authors",
         "duplicate_of", "pdf_url", "url"]
    )
    for p in result.relevant_papers:
        writer.writerow(
            [
                p.paper_id,
                p.title,
                "; ".join(topics.get(tid, "") for tid in p.topic_ids),
                f"{p.confidence:.2f}" if p.confidence is not None else "",
                "; ".join(p.authors),
                p.duplicate_of or "",
                p.pdf_url,
                p.url,
            ]
        )
    return buf.getvalue().encode("utf-8")
