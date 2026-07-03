"""Topic modelling over the theme-relevant papers.

Two backends:

* ``llm`` (default) — asks the model to derive a small topic taxonomy from the
  selected papers, then assigns each paper to a topic. No heavy ML dependencies,
  works anywhere the classifier works, and produces human-readable topic names.
* ``bertopic`` (optional) — classic embedding + clustering via the ``bertopic``
  package, if it is installed. Falls back to ``llm`` with a warning otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Callable, Optional

from .llm import LLMClient
from .models import Paper, Topic

_ABSTRACT_CHARS = 700
_ASSIGN_BATCH = 25

_DISCOVER_SYSTEM = (
    "You are a research analyst building a topic taxonomy. Given a set of papers that "
    "all relate to one broad theme, identify the most salient, distinct sub-topics. "
    "Topics must be specific enough to be useful and broad enough to group several "
    "papers. Avoid overlap; prefer descriptive, conference-track-style names."
)

_DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

_ASSIGN_SYSTEM = (
    "You assign each paper to exactly one topic from a fixed list. Choose the single "
    "best-fitting topic by the paper's core contribution. Use the topic's `id`."
)

_ASSIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "topic_id": {"type": "integer"},
                },
                "required": ["index", "topic_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assignments"],
    "additionalProperties": False,
}


def _short(paper: Paper) -> str:
    abstract = (paper.abstract or "").strip()
    if len(abstract) > _ABSTRACT_CHARS:
        abstract = abstract[:_ABSTRACT_CHARS] + "…"
    return f"{paper.title}. {abstract}".strip()


def _discover_topics(
    client: LLMClient, theme: str, papers: list[Paper], n_topics: int
) -> list[Topic]:
    lines = [
        f'Theme: "{theme}"',
        f"Derive between {max(2, n_topics - 2)} and {n_topics} distinct sub-topics that "
        "best organise the following papers. Return a name and a one-sentence "
        "description for each.",
        "",
    ]
    for p in papers:
        lines.append(f"- {p.title}")
    data = client.structured(
        _DISCOVER_SYSTEM, "\n".join(lines), _DISCOVER_SCHEMA, max_tokens=2000, effort="medium"
    )
    topics = []
    for i, t in enumerate(data.get("topics", [])[:n_topics]):
        topics.append(Topic(topic_id=i, name=t["name"], description=t.get("description", "")))
    return topics


def _assign_topics(
    client: LLMClient,
    papers: list[Paper],
    topics: list[Topic],
    progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    topic_menu = "\n".join(f"[{t.topic_id}] {t.name}: {t.description}" for t in topics)
    total = len(papers)
    done = 0
    if progress:
        progress(0, total)
    for start in range(0, total, _ASSIGN_BATCH):
        batch = papers[start : start + _ASSIGN_BATCH]
        lines = ["Topics:", topic_menu, "", "Papers:"]
        for i, p in enumerate(batch):
            lines.append(f"[{start + i}] {_short(p)}")
        data = client.structured(
            _ASSIGN_SYSTEM, "\n".join(lines), _ASSIGN_SCHEMA, max_tokens=3000, effort="low"
        )
        by_index = {a["index"]: a["topic_id"] for a in data.get("assignments", [])}
        valid_ids = {t.topic_id for t in topics}
        for i, paper in enumerate(batch):
            tid = by_index.get(start + i)
            paper.topic_id = tid if tid in valid_ids else topics[0].topic_id
        done += len(batch)
        if progress:
            progress(min(done, total), total)


def model_topics_llm(
    client: LLMClient,
    theme: str,
    papers: list[Paper],
    n_topics: int = 8,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[Topic]:
    if not papers:
        return []
    topics = _discover_topics(client, theme, papers, n_topics)
    if not topics:
        topics = [Topic(topic_id=0, name=theme, description="All selected papers.")]
    _assign_topics(client, papers, topics, progress)
    by_id = {t.topic_id: t for t in topics}
    for p in papers:
        if p.topic_id in by_id:
            by_id[p.topic_id].paper_ids.append(p.paper_id)
    # Drop empty topics and renumber so the UI is tidy.
    non_empty = [t for t in topics if t.count > 0]
    non_empty.sort(key=lambda t: t.count, reverse=True)
    remap = {t.topic_id: i for i, t in enumerate(non_empty)}
    for p in papers:
        if p.topic_id in remap:
            p.topic_id = remap[p.topic_id]
    for i, t in enumerate(non_empty):
        t.topic_id = i
    return non_empty


def model_topics_bertopic(
    theme: str, papers: list[Paper], n_topics: int = 8, **_: object
) -> list[Topic]:
    """Optional BERTopic backend. Requires the ``bertopic`` extra to be installed."""
    try:
        from bertopic import BERTopic  # type: ignore
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "BERTopic backend selected but the 'bertopic' package is not installed. "
            "Install it with `pip install bertopic`, or use the LLM backend."
        ) from e

    docs = [_short(p) for p in papers]
    model = BERTopic(nr_topics=n_topics, calculate_probabilities=False)
    labels, _probs = model.fit_transform(docs)
    info = {row["Topic"]: row["Name"] for _, row in model.get_topic_info().iterrows()}

    topics: dict[int, Topic] = {}
    next_id = 0
    id_map: dict[int, int] = {}
    for paper, label in zip(papers, labels):
        if label not in id_map:
            id_map[label] = next_id
            name = info.get(label, f"Topic {label}")
            topics[next_id] = Topic(topic_id=next_id, name=str(name), description="")
            next_id += 1
        tid = id_map[label]
        paper.topic_id = tid
        topics[tid].paper_ids.append(paper.paper_id)
    ordered = sorted(topics.values(), key=lambda t: t.count, reverse=True)
    remap = {t.topic_id: i for i, t in enumerate(ordered)}
    for p in papers:
        if p.topic_id in remap:
            p.topic_id = remap[p.topic_id]
    for i, t in enumerate(ordered):
        t.topic_id = i
    return ordered


def model_topics(
    backend: str,
    client: Optional[LLMClient],
    theme: str,
    papers: list[Paper],
    n_topics: int = 8,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[Topic]:
    if backend == "bertopic":
        return model_topics_bertopic(theme, papers, n_topics)
    assert client is not None
    return model_topics_llm(client, theme, papers, n_topics, progress)


# ---------------------------------------------------------------------------
# Per-topic synthesis: description + common findings
# ---------------------------------------------------------------------------
_SUMMARY_MAX_PAPERS = 30      # cap papers per prompt to bound tokens
_SUMMARY_ABSTRACT_CHARS = 600

_SUMMARY_SYSTEM = (
    "You are a research analyst synthesising a cluster of related papers. Write a "
    "short description of what the cluster is collectively about, then extract the "
    "MAIN FINDINGS that are COMMON across the papers — shared methods, recurring "
    "results, consensus positions, common problems or approaches. Focus on what "
    "recurs across multiple papers, NOT points specific to a single paper. Each "
    "finding must be a single, self-contained sentence."
)

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "findings"],
    "additionalProperties": False,
}


def _summary_prompt(theme: str, topic: Topic, papers: list[Paper]) -> str:
    lines = [
        f'Theme: "{theme}"',
        f'Topic: "{topic.name}"',
        "",
        "Based on the papers below, return:",
        "1. `description`: 1–2 sentences on what this group of papers is collectively about.",
        "2. `findings`: 5 to 10 bullet points capturing the MAIN findings COMMON across "
        "these papers (shared methods, recurring results, consensus, common "
        "challenges). Prefer cross-cutting themes over any single paper's specifics.",
        "",
        "Papers:",
    ]
    for p in papers:
        abstract = (p.abstract or "").strip()
        if len(abstract) > _SUMMARY_ABSTRACT_CHARS:
            abstract = abstract[:_SUMMARY_ABSTRACT_CHARS] + "…"
        lines.append(f"- {p.title}. {abstract}".rstrip())
    if len(topic.paper_ids) > _SUMMARY_MAX_PAPERS:
        lines.append(
            f"\n(Showing {_SUMMARY_MAX_PAPERS} of {len(topic.paper_ids)} papers; "
            "synthesise the common themes.)"
        )
    return "\n".join(lines)


def _topic_signature(topic: Topic) -> str:
    raw = topic.name + "|" + "|".join(sorted(topic.paper_ids))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _summary_cache_path(cache_dir: str, cache_sig: str, theme: str) -> str:
    digest = hashlib.sha1(f"{cache_sig}|{theme}|topicsum".encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"topicsum_{digest}.json")


def summarize_topics(
    client: LLMClient,
    theme: str,
    topics: list[Topic],
    papers: list[Paper],
    progress: Optional[Callable[[int, int], None]] = None,
    cache_dir: Optional[str] = None,
    cache_sig: str = "",
    force_refresh: bool = False,
) -> None:
    """Fill each topic's ``description`` and ``findings`` in place.

    Cached on disk keyed by provider+model, theme, and the topic's exact paper
    membership — so re-running the same analysis reuses summaries, while a topic
    whose papers changed is re-summarised.
    """
    by_id = {p.paper_id: p for p in papers}

    cache: dict[str, dict] = {}
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = _summary_cache_path(cache_dir, cache_sig, theme)
        if not force_refresh and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    cache = json.load(fh)
            except (OSError, json.JSONDecodeError):
                cache = {}

    total = len(topics)
    if progress:
        progress(0, total)

    for i, topic in enumerate(topics):
        sig = _topic_signature(topic)
        hit = cache.get(sig) if not force_refresh else None
        if hit:
            topic.description = hit.get("description", topic.description)
            topic.findings = list(hit.get("findings", []))
        else:
            batch = [by_id[pid] for pid in topic.paper_ids if pid in by_id]
            batch = batch[:_SUMMARY_MAX_PAPERS]
            try:
                data = client.structured(
                    _SUMMARY_SYSTEM,
                    _summary_prompt(theme, topic, batch),
                    _SUMMARY_SCHEMA,
                    max_tokens=1500,
                    effort="medium",
                )
                topic.description = data.get("description", topic.description) or topic.description
                topic.findings = [str(f) for f in data.get("findings", [])][:10]
                cache[sig] = {"description": topic.description, "findings": topic.findings}
                if cache_path:
                    try:
                        with open(cache_path, "w", encoding="utf-8") as fh:
                            json.dump(cache, fh)
                    except OSError:
                        pass
            except Exception:
                # A failed summary shouldn't sink the whole run; keep the topic as-is.
                pass
        if progress:
            progress(i + 1, total)
