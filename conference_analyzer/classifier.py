"""LLM-based relevance classification of papers against a theme."""

from __future__ import annotations

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


def _batch_prompt(theme: str, batch: list[Paper], offset: int) -> str:
    lines = [
        f'Theme: "{theme}"',
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


def classify_papers(
    client: LLMClient,
    theme: str,
    papers: list[Paper],
    min_confidence: float = 0.5,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[Paper]:
    """Annotate every paper with relevance/confidence/reason in place.

    Returns the list of papers judged relevant at or above ``min_confidence``.
    """
    total = len(papers)
    if progress:
        progress(0, total)

    done = 0
    for start in range(0, total, _BATCH_SIZE):
        batch = papers[start : start + _BATCH_SIZE]
        prompt = _batch_prompt(theme, batch, start)
        data = client.structured(_SYSTEM, prompt, _SCHEMA, max_tokens=4000, effort="low")
        by_index = {r["index"]: r for r in data.get("results", [])}
        for i, paper in enumerate(batch):
            r = by_index.get(start + i)
            if r is None:
                paper.relevant = False
                paper.confidence = 0.0
                continue
            paper.confidence = float(r.get("confidence", 0.0))
            paper.reason = r.get("reason", "")
            paper.relevant = bool(r.get("relevant")) and paper.confidence >= min_confidence
        done += len(batch)
        if progress:
            progress(min(done, total), total)

    return [p for p in papers if p.relevant]
