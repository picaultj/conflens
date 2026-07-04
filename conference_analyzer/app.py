"""NiceGUI front-end for the conference paper analyzer."""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import os
import re
import time
from typing import Optional

from nicegui import ui

from .cache import default_cache_dir
from .llm import DEFAULT_MODELS, MODEL_SUGGESTIONS, PROVIDERS, env_key_for
from .models import AnalysisResult
from .pipeline import AnalysisConfig, Progress, run_analysis
from .sources import SOURCES

# Sober, professional palette ------------------------------------------------
PRIMARY = "#1f4e79"   # deep navy
ACCENT = "#2b6cb0"
INK = "#1a202c"
MUTED = "#64748b"
LINE = "#e2e8f0"
TOPIC_COLORS = [
    "#1f4e79", "#2b6cb0", "#3182ce", "#0b7285", "#2f855a",
    "#975a16", "#9b2c2c", "#6b46c1", "#b83280", "#4a5568",
]

_CACHE_DIR = default_cache_dir()


class AnalyzerUI:
    def __init__(self) -> None:
        self.progress = Progress()
        self.result: Optional[AnalysisResult] = None
        self.timer: Optional[ui.timer] = None
        self.running = False

        # widgets populated in build()
        self.run_btn: Optional[ui.button] = None
        self.progress_card: Optional[ui.card] = None
        self.bar: Optional[ui.linear_progress] = None
        self.status_label: Optional[ui.label] = None
        self.log_area: Optional[ui.log] = None
        self.cancel_btn: Optional[ui.button] = None
        self.elapsed_label: Optional[ui.label] = None
        self._t0: float = 0.0
        self.results_container: Optional[ui.column] = None
        self.chart_container: Optional[ui.column] = None
        self.topics_container: Optional[ui.column] = None
        self.filter_status: Optional[ui.label] = None
        # results-view controls (populated when results are rendered)
        self.search: Optional[ui.input] = None
        self.global_toggle: Optional[ui.switch] = None
        self.sort_select: Optional[ui.select] = None
        self.author_select: Optional[ui.select] = None
        self.conf_view: Optional[ui.slider] = None
        self.conf_view_label: Optional[ui.label] = None

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def build(self) -> None:
        ui.colors(primary=PRIMARY)
        ui.query("body").style(f"background-color:#ffffff; color:{INK};")
        ui.add_head_html(
            "<style>"
            ".ca-card{border:1px solid %s;border-radius:10px;background:#fff;"
            "box-shadow:0 1px 2px rgba(16,24,40,.04);}"
            ".ca-muted{color:%s;}"
            ".ca-badge{background:%s;color:#fff;border-radius:6px;padding:2px 8px;"
            "font-size:.72rem;font-weight:600;text-decoration:none;}"
            "a.ca-title{color:%s;font-weight:600;text-decoration:none;}"
            "a.ca-title:hover{text-decoration:underline;}"
            "mark{background:#fde68a;color:inherit;padding:0 1px;border-radius:2px;}"
            "</style>" % (LINE, MUTED, ACCENT, INK)
        )

        # Header
        with ui.header().classes("items-center").style(
            f"background:{PRIMARY}; box-shadow:none; padding:14px 28px;"
        ):
            ui.icon("hub").classes("text-white").style("font-size:26px;")
            with ui.column().classes("gap-0"):
                ui.label("Conference Paper Analyzer").classes("text-white text-h6").style(
                    "font-weight:700; line-height:1.1;"
                )
                ui.label("Browse · classify by theme · discover topics").classes(
                    "text-white"
                ).style("opacity:.8; font-size:.8rem;")

        with ui.column().classes("w-full items-center").style("padding:24px 12px;"):
            with ui.column().style("width:100%; max-width:1080px; gap:18px;"):
                self._build_config()
                self._build_progress()
                self.results_container = ui.column().style("width:100%; gap:18px;")

    def _build_config(self) -> None:
        with ui.card().classes("w-full ca-card").style("padding:20px;"):
            ui.label("Configuration").style(
                f"font-weight:700; color:{INK}; font-size:1.05rem;"
            )
            with ui.row().classes("w-full").style("gap:16px; flex-wrap:wrap;"):
                self.source = ui.select(
                    {k: v["label"] for k, v in SOURCES.items()},
                    value="aclanthology",
                    label="Source",
                ).props("outlined dense").style("flex:1 1 150px;")
                self.base_url = ui.input(
                    SOURCES["aclanthology"]["base_label"],
                    value=SOURCES["aclanthology"]["base"],
                ).props("outlined dense").style("flex:2 1 240px;")
                self.event = ui.input(
                    SOURCES["aclanthology"]["target_label"],
                    value=SOURCES["aclanthology"]["target"],
                ).props("outlined dense").style("flex:1 1 200px;")
            self.source.on_value_change(lambda e: self._on_source_change(e.value))
            with ui.row().classes("w-full").style("gap:16px; flex-wrap:wrap;"):
                self.theme = ui.input("Theme", value="Agentic AI").props(
                    "outlined dense"
                ).style("flex:2 1 220px;")
                self.provider = ui.select(
                    PROVIDERS, value="litellm", label="LLM provider"
                ).props("outlined dense").style("flex:1 1 150px;")
                self.model = ui.input(
                    "Model", value=DEFAULT_MODELS["litellm"]
                ).props("outlined dense").style("flex:1 1 200px;")
                self.backend = ui.select(
                    {"llm": "LLM topics", "bertopic": "BERTopic"},
                    value="llm",
                    label="Topic engine",
                ).props("outlined dense").style("flex:1 1 150px;")
            self.theme_definition = ui.input(
                "Theme definition (optional)",
                placeholder="Clarify what counts as this theme — what to include / exclude",
            ).props("outlined dense").classes("w-full")
            with ui.row().classes("w-full items-center").style("gap:16px; flex-wrap:wrap;"):
                self.llm_base_url = ui.input(
                    "LLM endpoint (LiteLLM / OpenAI-compatible)",
                    value=os.environ.get("OPENAI_BASE_URL", ""),
                ).props("outlined dense clearable").style("flex:2 1 320px;")
                self.api_key = ui.input("API key (optional — overrides env var)", value="").props(
                    "outlined dense type=password clearable"
                ).style("flex:1 1 240px;")
            self.key_hint = ui.label("").classes("ca-muted").style("font-size:.78rem;")
            self.provider.on_value_change(lambda e: self._on_provider_change(e.value))
            self._on_provider_change("litellm")
            with ui.row().classes("w-full items-center").style("gap:24px; flex-wrap:wrap;"):
                with ui.column().style("flex:1 1 220px; gap:2px;"):
                    ui.label("Max papers to scan").classes("ca-muted").style("font-size:.8rem;")
                    self.max_papers = ui.number(value=150, min=1, max=10000, step=10).props(
                        "outlined dense"
                    ).classes("w-full")
                with ui.column().style("flex:1 1 220px; gap:2px;"):
                    ui.label("Target number of topics").classes("ca-muted").style(
                        "font-size:.8rem;"
                    )
                    self.n_topics = ui.number(value=8, min=2, max=20, step=1).props(
                        "outlined dense"
                    ).classes("w-full")
                with ui.column().style("flex:1 1 240px; gap:2px;"):
                    self.conf_label = ui.label("Min. confidence: 0.50").classes(
                        "ca-muted"
                    ).style("font-size:.8rem;")
                    self.min_conf = ui.slider(min=0, max=1, step=0.05, value=0.5).props(
                        "label-always"
                    )
                    self.min_conf.on_value_change(
                        lambda e: self.conf_label.set_text(f"Min. confidence: {e.value:.2f}")
                    )
            with ui.row().classes("w-full items-center justify-between").style(
                "margin-top:6px;"
            ):
                self.refresh = ui.checkbox("Refresh from source (ignore cache)", value=False)
                self.refresh.tooltip(
                    "Scraped listings/abstracts AND classification results are cached on "
                    "disk and reused across runs (re-running the same theme + model is "
                    "instant). Tick this to refetch and re-classify from scratch."
                )
                with ui.row().classes("items-center").style("gap:10px;"):
                    self.load_upload = (
                        ui.upload(on_upload=self._load_run, auto_upload=True)
                        .props('accept=.json flat dense label="Load saved run (.json)"')
                        .classes("max-w-[220px]")
                    )
                    self.load_upload.tooltip(
                        "Reload a run you saved earlier with the JSON export — no re-analysis."
                    )
                    self.run_btn = ui.button(
                        "Analyze", icon="play_arrow", on_click=self.start
                    ).props("unelevated")

    def _load_run(self, e) -> None:
        """Restore a previously saved run (JSON export) and render it — no re-run."""
        try:
            raw = e.content.read()
            data = json.loads(raw.decode("utf-8"))
            result = AnalysisResult.from_dict(data)
        except Exception as ex:  # malformed / wrong file
            ui.notify(f"Could not load run: {ex}", type="negative", multi_line=True)
            return
        finally:
            self.load_upload.reset()
        if not result.relevant_papers and not result.topics:
            ui.notify("That file doesn't look like a saved analysis run.", type="warning")
            return
        self.result = result
        if self.progress_card:
            self.progress_card.style("display:none;")
        ui.notify(
            f"Loaded run: “{result.theme}” · {len(result.relevant_papers)} papers.",
            type="positive",
        )
        self._render_results(result)

    def _on_source_change(self, source: str) -> None:
        """Prefill the base URL / target and relabel them for the chosen source."""
        cfg = SOURCES.get(source)
        if not cfg:
            return
        self.base_url.set_value(cfg["base"])
        self.base_url.props(f'label="{cfg["base_label"]}"')
        self.event.set_value(cfg["target"])
        self.event.props(f'label="{cfg["target_label"]}"')

    def _on_provider_change(self, provider: str) -> None:
        """Update the default model, endpoint relevance and key hint per provider."""
        self.model.set_value(DEFAULT_MODELS.get(provider, ""))
        suggestions = ", ".join(MODEL_SUGGESTIONS.get(provider, []))
        self.model.props(f'placeholder="{suggestions}"')
        needs_endpoint = provider == "litellm"
        self.llm_base_url.props(
            "outlined dense clearable"
            + (" required" if needs_endpoint else "")
        )
        has_env = bool(env_key_for(provider))
        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "litellm": "LITELLM_API_KEY / OPENAI_API_KEY",
        }.get(provider, "")
        parts = [f"Models e.g.: {suggestions}." if suggestions else ""]
        if has_env:
            parts.append(f"Using {env_var} from the environment.")
        else:
            parts.append(f"No {env_var} found — set it or fill the API key field.")
        if needs_endpoint:
            parts.append("LiteLLM: enter your endpoint above.")
        self.key_hint.set_text("  ".join(p for p in parts if p))

    def _build_progress(self) -> None:
        self.progress_card = ui.card().classes("w-full ca-card").style(
            "padding:18px; display:none;"
        )
        with self.progress_card:
            with ui.row().classes("w-full items-center justify-between").style("gap:12px;"):
                self.status_label = ui.label("").style(f"font-weight:600; color:{INK};")
                with ui.row().classes("items-center").style("gap:10px;"):
                    self.elapsed_label = ui.label("").classes("ca-muted").style(
                        "font-size:.8rem; font-variant-numeric:tabular-nums;"
                    )
                    self.cancel_btn = ui.button(
                        "Cancel", icon="stop", on_click=self._cancel
                    ).props("outline dense color=negative")
            self.bar = ui.linear_progress(value=0, show_value=False).props("rounded")
            with ui.expansion("Activity log").classes("w-full").style("margin-top:4px;"):
                self.log_area = ui.log(max_lines=200).classes("w-full").style(
                    "height:160px; font-family:ui-monospace,monospace; font-size:.78rem;"
                )

    # ------------------------------------------------------------------ #
    # Run lifecycle
    # ------------------------------------------------------------------ #
    def _validate(self) -> Optional[str]:
        """Return a user-facing error if the form isn't ready to run, else None."""
        if not (self.model.value or "").strip():
            return "Please set a model before running."
        if not (self.event.value or "").strip():
            return "Please set the event / accepted-papers target."
        if self.provider.value == "litellm" and not (self.llm_base_url.value or "").strip():
            return "LiteLLM needs an LLM endpoint — fill the “LLM endpoint” field."
        return None

    async def start(self) -> None:
        if self.running:
            return

        # Validate inputs before doing any work.
        error = self._validate()
        if error:
            ui.notify(error, type="warning")
            return

        self.running = True
        self.progress = Progress()
        self.result = None
        self._logged = 0
        self._t0 = time.monotonic()
        self.run_btn.props("loading")
        self.cancel_btn.props(remove="disable")
        self.results_container.clear()
        self.progress_card.style("display:block;")
        self.bar.set_value(0)
        self.status_label.set_text("Starting…")
        self.elapsed_label.set_text("0:00")
        self.log_area.clear()

        cfg = AnalysisConfig(
            source=self.source.value,
            base_url=self.base_url.value.strip(),
            event=self.event.value.strip(),
            theme=self.theme.value.strip() or "Agentic AI",
            theme_definition=(self.theme_definition.value or "").strip(),
            provider=self.provider.value,
            model=(self.model.value or "").strip(),
            llm_base_url=(self.llm_base_url.value or "").strip(),
            api_key=(self.api_key.value or "").strip(),
            max_papers=int(self.max_papers.value or 150),
            n_topics=int(self.n_topics.value or 8),
            min_confidence=float(self.min_conf.value),
            topic_backend=self.backend.value,
            refresh=bool(self.refresh.value),
        )

        self.timer = ui.timer(0.25, self._tick)
        try:
            self.result = await asyncio.to_thread(
                run_analysis, cfg, self.progress, _CACHE_DIR
            )
        except Exception as e:  # surface any failure to the user
            self.progress.error = str(e)
        finally:
            if self.timer:
                self.timer.cancel()
            self._tick()  # final flush
            self.running = False
            self.run_btn.props(remove="loading")
            self.cancel_btn.props("disable")

        if self.progress.error:
            self.status_label.set_text(f"Error: {self.progress.error}")
            ui.notify(self.progress.error, type="negative", multi_line=True)
            return
        if self.progress.cancelled:
            self.status_label.set_text("Cancelled.")
            ui.notify("Analysis cancelled.", type="info")
            return
        if self.result:
            self._render_results(self.result)

    def _cancel(self) -> None:
        if self.running:
            self.progress.cancelled = True
            self.status_label.set_text("Cancelling…")
            self.cancel_btn.props("disable")

    def _tick(self) -> None:
        # flush new log lines
        while self._logged < len(self.progress.log):
            self.log_area.push(self.progress.log[self._logged])
            self._logged += 1
        if self.progress.message:
            self.status_label.set_text(self.progress.message)
        self.bar.set_value(self.progress.fraction)
        if self.running:
            secs = int(time.monotonic() - self._t0)
            self.elapsed_label.set_text(f"{secs // 60}:{secs % 60:02d}")

    # ------------------------------------------------------------------ #
    # Results rendering
    # ------------------------------------------------------------------ #
    def _render_results(self, result: AnalysisResult) -> None:
        self.results_container.clear()
        with self.results_container:
            if not result.relevant_papers:
                with ui.card().classes("w-full ca-card").style("padding:24px;"):
                    ui.label("No matching papers").style(
                        f"font-weight:700; color:{INK}; font-size:1.05rem;"
                    )
                    ui.label(
                        f"Scanned {result.scanned} papers but found none about "
                        f"“{result.theme}”."
                    ).classes("ca-muted")
                return

            self._render_summary(result)
            self.chart_container = ui.column().classes("w-full").style("gap:0;")
            self._render_controls(result)
            self.topics_container = ui.column().style("width:100%; gap:18px;")
        self._apply_view()

    def _render_summary(self, result: AnalysisResult) -> None:
        with ui.card().classes("w-full ca-card").style("padding:18px 22px;"):
            with ui.row().classes("w-full items-center justify-between").style(
                "flex-wrap:wrap; gap:12px;"
            ):
                with ui.row().style("gap:36px; flex-wrap:wrap;"):
                    self._stat(str(result.scanned), "Papers scanned")
                    self._stat(str(len(result.relevant_papers)), f"Relevant to “{result.theme}”")
                    self._stat(str(len(result.topics)), "Topics")
                    if result.duplicate_groups:
                        self._stat(str(result.duplicate_groups), "Near-duplicate groups")
                with ui.row().style("gap:8px;"):
                    ui.button(
                        "PPTX", icon="slideshow", on_click=self._download_pptx
                    ).props("unelevated dense")
                    ui.button("JSON", icon="download", on_click=self._download_json).props(
                        "outline dense"
                    ).tooltip("Save this run — reload it later with “Load saved run”.")
                    ui.button("CSV", icon="download", on_click=self._download_csv).props(
                        "outline dense"
                    )
                    ui.button("BibTeX", icon="download", on_click=self._download_bibtex).props(
                        "outline dense"
                    )
            ui.link(
                "Source: " + result.event_url, result.event_url, new_tab=True
            ).classes("ca-muted").style("font-size:.8rem;")

    def _stat(self, value: str, label: str) -> None:
        with ui.column().classes("gap-0 items-start"):
            ui.label(value).style(f"font-size:1.8rem; font-weight:700; color:{PRIMARY};")
            ui.label(label).classes("ca-muted").style("font-size:.8rem;")

    def _render_chart(self, names: list[str], counts: list[int]) -> None:
        with ui.card().classes("w-full ca-card").style("padding:18px 22px;"):
            ui.label("Papers per topic").style(f"font-weight:700; color:{INK};")
            ui.echart(
                {
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "grid": {"left": 8, "right": 24, "top": 16, "bottom": 8, "containLabel": True},
                    "xAxis": {"type": "value", "minInterval": 1},
                    "yAxis": {
                        "type": "category",
                        "data": list(reversed(names)),
                        "axisLabel": {"width": 220, "overflow": "truncate"},
                    },
                    "series": [
                        {
                            "type": "bar",
                            "data": [
                                {"value": c, "itemStyle": {"color": TOPIC_COLORS[i % len(TOPIC_COLORS)]}}
                                for i, c in reversed(list(enumerate(counts)))
                            ],
                            "barMaxWidth": 26,
                            "label": {"show": True, "position": "right"},
                        }
                    ],
                }
            ).style(f"height:{max(160, 46 * len(names))}px; width:100%;")

    # ------------------------------------------------------------------ #
    # Results-view controls (all filter/sort live, client-side)
    # ------------------------------------------------------------------ #
    def _render_controls(self, result: AnalysisResult) -> None:
        authors = sorted(
            {a for p in result.relevant_papers for a in p.authors if a},
            key=str.casefold,
        )
        with ui.card().classes("w-full ca-card").style("padding:12px 18px;"):
            with ui.row().classes("w-full items-center").style("gap:12px; flex-wrap:wrap;"):
                ui.icon("search").style(f"color:{MUTED};")
                self.search = (
                    ui.input(
                        placeholder="Filter by keywords in the title or abstract "
                        "(comma-separated; each keyword may contain spaces)"
                    )
                    .props("dense clearable")
                    .style("flex:1 1 300px;")
                )
                self.search.on_value_change(lambda _: self._apply_view())
                self.global_toggle = ui.switch("Search all topics").props("dense")
                self.global_toggle.tooltip(
                    "Show every matching paper in one ranked list instead of grouping by topic."
                )
                self.global_toggle.on_value_change(lambda _: self._apply_view())
            with ui.row().classes("w-full items-center").style(
                "gap:16px; flex-wrap:wrap; margin-top:8px;"
            ):
                self.sort_select = (
                    ui.select(
                        {"confidence": "Confidence", "title": "Title", "year": "Year"},
                        value="confidence",
                        label="Sort by",
                    )
                    .props("dense outlined")
                    .style("flex:1 1 150px; max-width:200px;")
                )
                self.sort_select.on_value_change(lambda _: self._apply_view())
                self.author_select = (
                    ui.select(
                        authors,
                        value=None,
                        label="Filter by author",
                        with_input=True,
                        clearable=True,
                    )
                    .props("dense outlined")
                    .style("flex:1 1 220px; max-width:320px;")
                )
                self.author_select.on_value_change(lambda _: self._apply_view())
                with ui.column().classes("gap-0").style("flex:1 1 220px; min-width:200px;"):
                    self.conf_view_label = ui.label("").classes("ca-muted").style(
                        "font-size:.8rem;"
                    )
                    self.conf_view = ui.slider(
                        min=0, max=1, step=0.05, value=result.min_confidence
                    ).props("label-always")
                    self.conf_view.on_value_change(lambda _: self._apply_view())
                self.filter_status = ui.label("").classes("ca-muted").style(
                    "font-size:.8rem; white-space:nowrap;"
                )

    @staticmethod
    def _keywords(query: str) -> list[str]:
        # Split on commas so each keyword may itself contain spaces.
        return [k.strip().lower() for k in (query or "").split(",") if k.strip()]

    @staticmethod
    def _matches(paper, keywords: list[str]) -> bool:
        if not keywords:
            return True
        text = f"{paper.title}\n{paper.abstract or ''}".lower()
        return all(k in text for k in keywords)  # AND across keywords

    @staticmethod
    def _highlight(text: str, keywords: list[str]) -> str:
        esc = html.escape(text or "")
        if not keywords:
            return esc
        pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
        return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", esc)

    def _sorted(self, papers: list, sort: str) -> list:
        if sort == "title":
            return sorted(papers, key=lambda p: (p.title or "").casefold())
        if sort == "year":
            return sorted(papers, key=lambda p: (p.year or 0), reverse=True)
        return sorted(papers, key=lambda p: (p.confidence or 0), reverse=True)

    def _apply_view(self) -> None:
        """Recompute the filtered/sorted view and re-render chart + papers live."""
        result = self.result
        if result is None or self.topics_container is None:
            return
        query = self.search.value if self.search else ""
        keywords = self._keywords(query)
        min_conf = float(self.conf_view.value) if self.conf_view else 0.0
        author = (self.author_select.value or "") if self.author_select else ""
        sort = (self.sort_select.value or "confidence") if self.sort_select else "confidence"
        is_global = bool(self.global_toggle.value) if self.global_toggle else False

        by_id = {p.paper_id: p for p in result.relevant_papers}
        all_by_id = {p.paper_id: p for p in result.papers}  # for duplicate-rep titles
        topic_name = {t.topic_id: t.name for t in result.topics}

        def passes(p) -> bool:
            if (p.confidence or 0) < min_conf:
                return False
            if author and author not in p.authors:
                return False
            return self._matches(p, keywords)

        # Per-topic filtered papers (drives both the chart and the grouped view).
        per_topic = {
            t.topic_id: self._sorted(
                [by_id[pid] for pid in t.paper_ids if pid in by_id and passes(by_id[pid])],
                sort,
            )
            for t in result.topics
        }
        counts = [len(per_topic[t.topic_id]) for t in result.topics]

        # Chart reflects the live view.
        if self.chart_container is not None:
            self.chart_container.clear()
            with self.chart_container:
                self._render_chart([t.name for t in result.topics], counts)

        shown_papers = 0
        shown_topics = 0
        self.topics_container.clear()
        with self.topics_container:
            if is_global:
                shown_papers, shown_topics = self._render_global(
                    result, per_topic, topic_name, all_by_id, keywords, sort
                )
            else:
                shown_papers, shown_topics = self._render_grouped(
                    result, per_topic, topic_name, all_by_id, keywords
                )

        self._update_status(result, shown_papers, shown_topics, min_conf, is_global)

    def _update_status(self, result, shown_papers, shown_topics, min_conf, is_global) -> None:
        if self.conf_view_label is not None:
            self.conf_view_label.set_text(f"Show ≥ {min_conf:.2f} confidence")
        if self.filter_status is not None:
            scope = "in one list" if is_global else f"· {shown_topics} of {len(result.topics)} topics"
            self.filter_status.set_text(
                f"{shown_papers} of {len(result.relevant_papers)} papers {scope}"
            )

    def _render_grouped(self, result, per_topic, topic_name, all_by_id, keywords) -> tuple:
        active = bool(keywords)  # auto-open + hide-empty only when keyword filtering
        shown_papers = 0
        shown_topics = 0
        for t in result.topics:
            papers = per_topic[t.topic_id]
            if not papers:
                continue
            shown_papers += len(papers)
            shown_topics += 1
            color = TOPIC_COLORS[t.topic_id % len(TOPIC_COLORS)]
            badge = (
                f"{len(papers)} of {t.count}"
                if len(papers) != t.count
                else f"{t.count} paper{'s' if t.count != 1 else ''}"
            )
            with ui.card().classes("w-full ca-card").style("padding:0; overflow:hidden;"):
                with ui.expansion().classes("w-full").props(
                    "default-opened" if active else ""
                ) as exp:
                    with exp.add_slot("header"):
                        with ui.row().classes("w-full items-center").style("gap:12px;"):
                            ui.element("div").style(
                                f"width:10px; height:10px; border-radius:50%; background:{color};"
                            )
                            ui.label(t.name).style(f"font-weight:700; color:{INK};")
                            ui.label(badge).classes("ca-badge").style(f"background:{color};")
                    with ui.column().classes("w-full").style(
                        "padding:4px 18px 14px 18px; gap:10px;"
                    ):
                        if t.description:
                            ui.label(t.description).style(
                                f"color:{INK}; font-size:.9rem; line-height:1.5;"
                            )
                        if t.findings:
                            with ui.column().classes("w-full").style(
                                f"gap:4px; background:#f8fafc; border:1px solid {LINE}; "
                                "border-radius:8px; padding:12px 16px;"
                            ):
                                ui.label("Main findings across this topic").style(
                                    f"font-weight:700; color:{PRIMARY}; font-size:.8rem; "
                                    "text-transform:uppercase; letter-spacing:.04em;"
                                )
                                ui.markdown(
                                    "\n".join(f"- {f}" for f in t.findings)
                                ).style(f"color:{INK}; font-size:.85rem;")
                        ui.label(f"Papers ({len(papers)})").classes("ca-muted").style(
                            "font-size:.8rem; font-weight:600; margin-top:4px;"
                        )
                        for p in papers:
                            self._render_paper(
                                p, keywords,
                                also_in=self._also_in(p, t.topic_id, topic_name),
                                dup_title=self._dup_title(p, all_by_id),
                            )
        if shown_topics == 0:
            ui.label("No papers match the current filters.").classes("ca-muted").style(
                "padding:8px 2px;"
            )
        return shown_papers, shown_topics

    def _render_global(self, result, per_topic, topic_name, all_by_id, keywords, sort) -> tuple:
        # Flatten unique papers across topics (a multi-topic paper appears once).
        seen: dict = {}
        for t in result.topics:
            for p in per_topic[t.topic_id]:
                seen.setdefault(p.paper_id, p)
        papers = self._sorted(list(seen.values()), sort)
        with ui.card().classes("w-full ca-card").style("padding:6px 18px 14px 18px;"):
            ui.label(f"All matching papers ({len(papers)})").classes("ca-muted").style(
                "font-size:.8rem; font-weight:600; margin-top:8px;"
            )
            if not papers:
                ui.label("No papers match the current filters.").classes("ca-muted").style(
                    "padding:8px 2px;"
                )
            for p in papers:
                self._render_paper(
                    p, keywords,
                    also_in=[topic_name[tid] for tid in p.topic_ids if tid in topic_name],
                    dup_title=self._dup_title(p, all_by_id),
                    also_label="Topics: ",
                )
        return len(papers), 1 if papers else 0

    @staticmethod
    def _also_in(p, current_topic_id, topic_name) -> list:
        return [
            topic_name[tid]
            for tid in p.topic_ids
            if tid != current_topic_id and tid in topic_name
        ]

    @staticmethod
    def _dup_title(p, all_by_id) -> Optional[str]:
        if p.duplicate_of and p.duplicate_of in all_by_id:
            return all_by_id[p.duplicate_of].title
        return None

    def _render_paper(
        self, p, keywords=None, also_in=None, dup_title=None, also_label="Also in: "
    ) -> None:
        keywords = keywords or []
        with ui.column().classes("w-full").style(
            f"gap:3px; padding:10px 0; border-top:1px solid {LINE};"
        ):
            with ui.row().classes("w-full items-start justify-between").style("gap:10px;"):
                if keywords:
                    title_html = self._highlight(p.title, keywords)
                    if p.url:
                        ui.html(
                            f'<a href="{html.escape(p.url)}" target="_blank" '
                            f'rel="noopener" class="ca-title">{title_html}</a>'
                        )
                    else:
                        ui.html(f'<span style="color:{INK};font-weight:600;">{title_html}</span>')
                elif p.url:
                    ui.link(p.title, p.url, new_tab=True).classes("ca-title")
                else:
                    ui.label(p.title).style(f"color:{INK}; font-weight:600;")
                with ui.row().style("gap:6px; flex-wrap:nowrap;"):
                    if dup_title:
                        ui.label("near-dup").classes("ca-badge").style(
                            "background:#b7791f;"
                        ).tooltip(f"Near-duplicate of: {dup_title}")
                    if p.confidence is not None:
                        ui.label(f"{p.confidence:.0%}").classes("ca-badge").style(
                            f"background:{MUTED};"
                        ).tooltip("Relevance confidence")
                    if p.pdf_url:
                        ui.link("PDF", p.pdf_url, new_tab=True).classes("ca-badge")
            if p.authors:
                authors = ", ".join(p.authors[:6]) + ("…" if len(p.authors) > 6 else "")
                ui.label(authors).classes("ca-muted").style("font-size:.8rem;")
            if also_in:
                ui.label(also_label + ", ".join(also_in)).classes("ca-muted").style(
                    "font-size:.78rem;"
                )
            if p.abstract:
                with ui.expansion("Abstract").classes("w-full").style("font-size:.85rem;"):
                    if keywords:
                        ui.html(self._highlight(p.abstract, keywords)).style(
                            f"color:{INK}; font-size:.85rem; line-height:1.5;"
                        )
                    else:
                        ui.label(p.abstract).style(
                            f"color:{INK}; font-size:.85rem; line-height:1.5;"
                        )
            if p.reason:
                ui.label("Why: " + p.reason).classes("ca-muted").style(
                    "font-size:.78rem; font-style:italic;"
                )

    # ------------------------------------------------------------------ #
    # Exports
    # ------------------------------------------------------------------ #
    async def _download_pptx(self) -> None:
        if not self.result:
            return
        from .pptx_export import build_pptx

        ui.notify("Building slide deck…", type="ongoing")
        try:
            data = await asyncio.to_thread(build_pptx, self.result)
        except Exception as e:  # missing dependency or rendering failure
            ui.notify(str(e), type="negative", multi_line=True)
            return
        ui.download.content(
            data,
            "analysis.pptx",
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
        )

    def _download_json(self) -> None:
        if not self.result:
            return
        data = json.dumps(self.result.to_dict(), indent=2, ensure_ascii=False)
        ui.download.content(data.encode("utf-8"), "analysis.json")

    def _download_bibtex(self) -> None:
        if not self.result:
            return
        from .bibtex import build_bibtex

        ui.download.content(
            build_bibtex(self.result).encode("utf-8"),
            "papers.bib",
            media_type="application/x-bibtex",
        )

    def _download_csv(self) -> None:
        if not self.result:
            return
        topics = {t.topic_id: t.name for t in self.result.topics}
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["paper_id", "title", "topics", "confidence", "authors",
             "duplicate_of", "pdf_url", "url"]
        )
        for p in self.result.relevant_papers:
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
        ui.download.content(buf.getvalue().encode("utf-8"), "analysis.csv")


def create_ui() -> None:
    AnalyzerUI().build()
