"""LLM-based relevance classification of papers against a theme."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Callable, Optional

from .llm import LLMClient
from .models import Paper

_BATCH_SIZE = 20
_ABSTRACT_CHARS = 900  # keep prompts cheap; the title + opening of the abstract is plenty

_SYSTEM = (
    "You are an expert research librarian for NLP and machine-learning venues. "
    "You decide whether each paper is genuinely about a given theme. Judge by the "
    "paper's actual contribution, not incidental keyword matches. Be precise: a paper "
    "that merely mentions the theme in passing is NOT relevant; a paper whose core "
    "contribution advances the theme IS relevant."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "relevant", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _batch_prompt(theme: str, batch: list[Paper], offset: int, theme_definition: str = "") -> str:
    lines = [f'Theme: "{theme}"']
    if theme_definition and theme_definition.strip():
        lines.append(f"Scope of the theme (what counts / doesn't): {theme_definition.strip()}")
    lines += [
        "",
        "For EACH paper below, decide whether its core contribution is about this "
        "theme. Return relevant (true/false), a confidence in [0,1], and a one-sentence "
        "reason. Use the paper's `index` exactly as given.",
        "",
    ]
    for i, p in enumerate(batch):
        idx = offset + i
        abstract = (p.abstract or "").strip()
        if len(abstract) > _ABSTRACT_CHARS:
            abstract = abstract[:_ABSTRACT_CHARS] + "…"
        lines.append(f"[{idx}] Title: {p.title}")
        if abstract:
            lines.append(f"    Abstract: {abstract}")
        lines.append("")
    return "\n".join(lines)


def _content_hash(paper: Paper) -> str:
    raw = f"{paper.title}\n{paper.abstract or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_path(cache_dir: str, cache_sig: str, theme: str, theme_definition: str = "") -> str:
    digest = hashlib.sha1(
        f"{cache_sig}|{theme}|{theme_definition}".encode("utf-8")
    ).hexdigest()[:16]
    return os.path.join(cache_dir, f"classify_{digest}.json")


def classify_papers(
    client: LLMClient,
    theme: str,
    papers: list[Paper],
    min_confidence: float = 0.5,
    progress: Optional[Callable[[int, int], None]] = None,
    cache_dir: Optional[str] = None,
    cache_sig: str = "",
    force_refresh: bool = False,
    cancel: Optional[Callable[[], None]] = None,
    theme_definition: str = "",
) -> list[Paper]:
    """Annotate every paper with relevance/confidence/reason in place.

    The *raw* model judgement (relevant flag, confidence, reason) is cached on
    disk, keyed by provider+model, theme, and the paper's title+abstract hash —
    so re-running the same theme/model is free, while changing any of those
    re-classifies only the affected papers. ``min_confidence`` is applied at read
    time, so adjusting the threshold never needs a re-call.

    Returns the list of papers judged relevant at or above ``min_confidence``.
    """
    total = len(papers)

    # Load any cached judgements for this (provider+model, theme).
    cache: dict[str, dict] = {}
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = _cache_path(cache_dir, cache_sig, theme, theme_definition)
        if not force_refresh and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    cache = json.load(fh)
            except (OSError, json.JSONDecodeError):
                cache = {}

    # raw[paper_id] = {"relevant": bool, "confidence": float, "reason": str}
    raw: dict[str, dict] = {}
    todo: list[Paper] = []
    for p in papers:
        hit = cache.get(p.paper_id)
        if hit and not force_refresh and hit.get("hash") == _content_hash(p):
            raw[p.paper_id] = hit
        else:
            todo.append(p)

    done = total - len(todo)
    if progress:
        progress(done, total)

    def _save() -> None:
        if cache_path:
            try:
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump(cache, fh)
            except OSError:
                pass

    for start in range(0, len(todo), _BATCH_SIZE):
        if cancel:
            cancel()
        batch = todo[start : start + _BATCH_SIZE]
        prompt = _batch_prompt(theme, batch, start, theme_definition)
        data = client.structured(_SYSTEM, prompt, _SCHEMA, max_tokens=4000, effort="low")
        by_index = {r["index"]: r for r in data.get("results", [])}
        for i, paper in enumerate(batch):
            r = by_index.get(start + i) or {}
            entry = {
                "relevant": bool(r.get("relevant")),
                "confidence": float(r.get("confidence", 0.0)),
                "reason": r.get("reason", ""),
                "hash": _content_hash(paper),
            }
            raw[paper.paper_id] = entry
            cache[paper.paper_id] = entry
        done += len(batch)
        if progress:
            progress(min(done, total), total)
        _save()  # persist incrementally so a long run is resumable

    _save()

    # Apply the confidence threshold to every paper (cached + freshly judged).
    relevant: list[Paper] = []
    for p in papers:
        entry = raw.get(p.paper_id, {})
        p.confidence = float(entry.get("confidence", 0.0))
        p.reason = entry.get("reason", "")
        p.relevant = bool(entry.get("relevant")) and p.confidence >= min_confidence
        if p.relevant:
            relevant.append(p)
    return relevant
