"""Orchestrates scrape -> classify -> topic-model into one analysis run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import classifier, topics as topic_mod
from .llm import make_client
from .models import AnalysisResult
from .sources import make_source


@dataclass
class Progress:
    """Mutable progress state shared with the UI."""

    stage: str = "idle"
    message: str = ""
    fraction: float = 0.0          # 0..1 within the current stage
    log: list[str] = field(default_factory=list)
    done: bool = False
    error: Optional[str] = None

    def set(self, stage: str, message: str, fraction: float = 0.0) -> None:
        self.stage = stage
        self.message = message
        self.fraction = fraction
        self.log.append(message)


@dataclass
class AnalysisConfig:
    source: str = "aclanthology"   # "aclanthology" | "ijcai"
    base_url: str = "https://aclanthology.org"
    event: str = "acl-2026"
    theme: str = "Agentic AI"
    provider: str = "anthropic"    # "anthropic" | "openai" | "litellm"
    model: str = "claude-opus-4-8"
    llm_base_url: str = ""         # custom endpoint (LiteLLM / OpenAI-compatible)
    api_key: str = ""              # overrides the provider's env var if set
    max_papers: int = 150
    n_topics: int = 8
    min_confidence: float = 0.5
    topic_backend: str = "llm"     # "llm" | "bertopic"
    refresh: bool = False          # bypass the scrape cache and refetch from source


def run_analysis(
    cfg: AnalysisConfig,
    progress: Progress,
    cache_dir: Optional[str] = None,
) -> AnalysisResult:
    """Execute the full pipeline. Designed to run in a worker thread."""
    scraper = make_source(cfg.source, base_url=cfg.base_url, cache_dir=cache_dir)
    event_url = scraper.resolve_url(cfg.event)
    result = AnalysisResult(theme=cfg.theme, event_url=event_url)

    # 1. List papers ----------------------------------------------------
    origin = "source" if cfg.refresh else "cache or source"
    progress.set("listing", f"Fetching paper list from {event_url} ({origin})", 0.0)
    papers = scraper.list_papers(cfg.event, force_refresh=cfg.refresh)
    if not papers:
        hint = (
            " The proceedings may not be published yet — try a past event such "
            "as 'acl-2024'."
            if cfg.source == "aclanthology"
            else " Check the URL points at the accepted-papers page."
        )
        progress.set("listing", "No papers found on that page." + hint, 1.0)
        progress.done = True
        return result
    if cfg.max_papers and len(papers) > cfg.max_papers:
        papers = papers[: cfg.max_papers]
    progress.set("listing", f"Found {len(papers)} papers.", 1.0)

    # 2. Abstracts ------------------------------------------------------
    def abs_prog(done: int, total: int) -> None:
        progress.set("abstracts", f"Fetching abstracts ({done}/{total})", done / max(total, 1))

    scraper.enrich_abstracts(papers, progress=abs_prog, force_refresh=cfg.refresh)
    result.papers = papers
    result.scanned = len(papers)

    # 3. Classify -------------------------------------------------------
    client = make_client(cfg.provider, cfg.model, cfg.api_key, cfg.llm_base_url)

    def cls_prog(done: int, total: int) -> None:
        progress.set("classify", f"Classifying for '{cfg.theme}' ({done}/{total})", done / max(total, 1))

    relevant = classifier.classify_papers(
        client, cfg.theme, papers, min_confidence=cfg.min_confidence, progress=cls_prog
    )
    result.relevant_papers = relevant
    progress.set("classify", f"{len(relevant)} of {len(papers)} papers match the theme.", 1.0)

    if not relevant:
        progress.set("topics", "No matching papers — nothing to model.", 1.0)
        progress.done = True
        return result

    # 4. Topic modelling ------------------------------------------------
    def top_prog(done: int, total: int) -> None:
        progress.set("topics", f"Assigning topics ({done}/{total})", done / max(total, 1))

    progress.set("topics", "Discovering topics…", 0.0)
    result.topics = topic_mod.model_topics(
        cfg.topic_backend, client, cfg.theme, relevant, n_topics=cfg.n_topics, progress=top_prog
    )
    progress.set("done", f"Done — {len(result.topics)} topics across {len(relevant)} papers.", 1.0)
    progress.done = True
    return result
