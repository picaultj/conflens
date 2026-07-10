"""Orchestrates scrape -> classify -> topic-model into one analysis run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import classifier, dedup
from . import topics as topic_mod
from .llm import make_client
from .models import AnalysisResult
from .sources import make_source


class AnalysisCancelled(Exception):
    """Raised cooperatively when the user cancels a run."""


@dataclass
class Progress:
    """Mutable progress state shared with the UI."""

    stage: str = "idle"
    message: str = ""
    fraction: float = 0.0          # 0..1 within the current stage
    log: list[str] = field(default_factory=list)
    done: bool = False
    error: Optional[str] = None
    cancelled: bool = False        # set by the UI to request cooperative cancel

    def set(self, stage: str, message: str, fraction: float = 0.0) -> None:
        self.stage = stage
        self.message = message
        self.fraction = fraction
        self.log.append(message)

    def check_cancel(self) -> None:
        if self.cancelled:
            raise AnalysisCancelled()


@dataclass
class AnalysisConfig:
    source: str = "aclanthology"   # "aclanthology" | "ijcai"
    base_url: str = "https://aclanthology.org"
    event: str = "acl-2026"
    theme: str = "Agentic AI"
    theme_definition: str = ""     # optional clarification of what the theme includes/excludes
    provider: str = "openai"    # "anthropic" | "openai" | "litellm"
    model: str = "gpt-5.4"
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
    result = AnalysisResult(
        theme=cfg.theme, event_url=event_url, min_confidence=cfg.min_confidence
    )

    try:
        # 1. List papers ------------------------------------------------
        origin = "source" if cfg.refresh else "cache or source"
        progress.set("listing", f"Fetching paper list from {event_url} ({origin})", 0.0)
        papers = scraper.list_papers(cfg.event, force_refresh=cfg.refresh)
        if not papers:
            hints = {
                "aclanthology": (
                    " The proceedings may not be published yet — try a past event "
                    "such as 'acl-2024'."
                ),
                "emnlp": (
                    " The proceedings may not be published yet — try a past event "
                    "such as 'emnlp-2023'."
                ),
                "naacl": (
                    " The proceedings may not be published yet — try a past event "
                    "such as 'naacl-2024'."
                ),
                "ijcai": " Check the URL points at the accepted-papers page.",
                "openreview": (
                    " Check the venue id (e.g. 'ICLR.cc/2024/Conference' or "
                    "'NeurIPS.cc/2024/Conference') — decisions may not be posted yet."
                ),
                "pscc": (
                    " PSCC is biennial — try a year that was held, e.g. '2024' or "
                    "'2022'."
                ),
            }
            hint = hints.get(cfg.source, "")
            progress.set("listing", "No papers found on that page." + hint, 1.0)
            progress.done = True
            return result
        if cfg.max_papers and len(papers) > cfg.max_papers:
            papers = papers[: cfg.max_papers]
        progress.set("listing", f"Found {len(papers)} papers.", 1.0)
        progress.check_cancel()

        # 2. Abstracts --------------------------------------------------
        def abs_prog(done: int, total: int) -> None:
            progress.check_cancel()
            progress.set("abstracts", f"Fetching abstracts ({done}/{total})", done / max(total, 1))

        scraper.enrich_abstracts(papers, progress=abs_prog, force_refresh=cfg.refresh)
        result.papers = papers
        result.scanned = len(papers)
        result.duplicate_groups = dedup.annotate_duplicates(papers)
        if result.duplicate_groups:
            progress.set("listing", f"Flagged {result.duplicate_groups} near-duplicate group(s).", 1.0)
        progress.check_cancel()

        # 3. Classify ---------------------------------------------------
        client = make_client(cfg.provider, cfg.model, cfg.api_key, cfg.llm_base_url)

        def cls_prog(done: int, total: int) -> None:
            progress.set(
                "classify", f"Classifying for '{cfg.theme}' ({done}/{total})", done / max(total, 1)
            )

        # Keep every paper the model judges relevant (threshold 0) so the UI can
        # re-apply the confidence cut-off live without a re-run. The run's
        # ``min_confidence`` is carried on the result as the default display cut.
        relevant = classifier.classify_papers(
            client,
            cfg.theme,
            papers,
            min_confidence=0.0,
            progress=cls_prog,
            cache_dir=cache_dir,
            cache_sig=f"{cfg.provider}:{cfg.model}",
            force_refresh=cfg.refresh,
            cancel=progress.check_cancel,
            theme_definition=cfg.theme_definition,
        )
        result.relevant_papers = relevant
        at_threshold = sum(1 for p in relevant if (p.confidence or 0) >= cfg.min_confidence)
        progress.set(
            "classify",
            f"{at_threshold} of {len(papers)} papers match the theme "
            f"(≥{cfg.min_confidence:.2f} confidence).",
            1.0,
        )

        if not relevant:
            progress.set("topics", "No matching papers — nothing to model.", 1.0)
            progress.done = True
            return result

        # 4. Topic modelling --------------------------------------------
        def top_prog(done: int, total: int) -> None:
            progress.set("topics", f"Assigning topics ({done}/{total})", done / max(total, 1))

        progress.set("topics", "Discovering topics…", 0.0)
        result.topics = topic_mod.model_topics(
            cfg.topic_backend,
            client,
            cfg.theme,
            relevant,
            n_topics=cfg.n_topics,
            progress=top_prog,
            theme_definition=cfg.theme_definition,
        )
        progress.check_cancel()

        # 5. Per-topic synthesis (description + common findings) --------
        def sum_prog(done: int, total: int) -> None:
            progress.set("summarize", f"Summarising topics ({done}/{total})", done / max(total, 1))

        progress.set("summarize", "Summarising topics…", 0.0)
        topic_mod.summarize_topics(
            client,
            cfg.theme,
            result.topics,
            relevant,
            progress=sum_prog,
            cache_dir=cache_dir,
            cache_sig=f"{cfg.provider}:{cfg.model}",
            force_refresh=cfg.refresh,
            cancel=progress.check_cancel,
            theme_definition=cfg.theme_definition,
        )

        progress.set("done", f"Done — {len(result.topics)} topics across {len(relevant)} papers.", 1.0)
        progress.done = True
        return result
    except AnalysisCancelled:
        progress.set("cancelled", "Cancelled.", progress.fraction)
        progress.done = True
        return result
