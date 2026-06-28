"""NiceGUI front-end for the conference paper analyzer."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from typing import Optional

from nicegui import ui

from .cache import default_cache_dir
from .llm import DEFAULT_MODELS, MODEL_SUGGESTIONS, PROVIDERS, env_key_for
from .models import AnalysisResult
from .sources import SOURCES
from .pipeline import AnalysisConfig, Progress, run_analysis

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
        self.results_container: Optional[ui.column] = None

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
                self.run_btn = ui.button("Analyze", icon="play_arrow", on_click=self.start).props(
                    "unelevated"
                )

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
            self.status_label = ui.label("").style(f"font-weight:600; color:{INK};")
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
        self.run_btn.props("loading")
        self.results_container.clear()
        self.progress_card.style("display:block;")
        self.bar.set_value(0)
        self.status_label.set_text("Starting…")
        self.log_area.clear()

        cfg = AnalysisConfig(
            source=self.source.value,
            base_url=self.base_url.value.strip(),
            event=self.event.value.strip(),
            theme=self.theme.value.strip() or "Agentic AI",
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

        if self.progress.error:
            self.status_label.set_text(f"Error: {self.progress.error}")
            ui.notify(self.progress.error, type="negative", multi_line=True)
            return
        if self.result:
            self._render_results(self.result)

    def _tick(self) -> None:
        # flush new log lines
        while self._logged < len(self.progress.log):
            self.log_area.push(self.progress.log[self._logged])
            self._logged += 1
        if self.progress.message:
            self.status_label.set_text(self.progress.message)
        self.bar.set_value(self.progress.fraction)

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
            self._render_chart(result)
            self._render_topics(result)

    def _render_summary(self, result: AnalysisResult) -> None:
        with ui.card().classes("w-full ca-card").style("padding:18px 22px;"):
            with ui.row().classes("w-full items-center justify-between").style(
                "flex-wrap:wrap; gap:12px;"
            ):
                with ui.row().style("gap:36px; flex-wrap:wrap;"):
                    self._stat(str(result.scanned), "Papers scanned")
                    self._stat(str(len(result.relevant_papers)), f"Match “{result.theme}”")
                    self._stat(str(len(result.topics)), "Topics")
                with ui.row().style("gap:8px;"):
                    ui.button(
                        "PPTX", icon="slideshow", on_click=self._download_pptx
                    ).props("unelevated dense")
                    ui.button("JSON", icon="download", on_click=self._download_json).props(
                        "outline dense"
                    )
                    ui.button("CSV", icon="download", on_click=self._download_csv).props(
                        "outline dense"
                    )
            ui.link(
                "Source: " + result.event_url, result.event_url, new_tab=True
            ).classes("ca-muted").style("font-size:.8rem;")

    def _stat(self, value: str, label: str) -> None:
        with ui.column().classes("gap-0 items-start"):
            ui.label(value).style(f"font-size:1.8rem; font-weight:700; color:{PRIMARY};")
            ui.label(label).classes("ca-muted").style("font-size:.8rem;")

    def _render_chart(self, result: AnalysisResult) -> None:
        topics = result.topics
        names = [t.name for t in topics]
        counts = [t.count for t in topics]
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
            ).style(f"height:{max(160, 46 * len(topics))}px; width:100%;")

    def _render_topics(self, result: AnalysisResult) -> None:
        by_id = {p.paper_id: p for p in result.relevant_papers}
        for t in result.topics:
            color = TOPIC_COLORS[t.topic_id % len(TOPIC_COLORS)]
            with ui.card().classes("w-full ca-card").style("padding:0; overflow:hidden;"):
                with ui.expansion().classes("w-full") as exp:
                    with exp.add_slot("header"):
                        with ui.row().classes("w-full items-center").style("gap:12px;"):
                            ui.element("div").style(
                                f"width:10px; height:10px; border-radius:50%; background:{color};"
                            )
                            ui.label(t.name).style(f"font-weight:700; color:{INK};")
                            ui.label(f"{t.count} paper{'s' if t.count != 1 else ''}").classes(
                                "ca-badge"
                            ).style(f"background:{color};")
                    with ui.column().classes("w-full").style("padding:4px 18px 14px 18px; gap:10px;"):
                        if t.description:
                            ui.label(t.description).classes("ca-muted").style("font-size:.85rem;")
                        papers = [by_id[pid] for pid in t.paper_ids if pid in by_id]
                        papers.sort(key=lambda p: p.confidence or 0, reverse=True)
                        for p in papers:
                            self._render_paper(p)

    def _render_paper(self, p) -> None:
        with ui.column().classes("w-full").style(
            f"gap:3px; padding:10px 0; border-top:1px solid {LINE};"
        ):
            with ui.row().classes("w-full items-start justify-between").style("gap:10px;"):
                if p.url:
                    ui.link(p.title, p.url, new_tab=True).classes("ca-title")
                else:
                    ui.label(p.title).style(f"color:{INK}; font-weight:600;")
                with ui.row().style("gap:6px; flex-wrap:nowrap;"):
                    if p.confidence is not None:
                        ui.label(f"{p.confidence:.0%}").classes("ca-badge").style(
                            f"background:{MUTED};"
                        ).tooltip("Relevance confidence")
                    if p.pdf_url:
                        ui.link("PDF", p.pdf_url, new_tab=True).classes("ca-badge")
            if p.authors:
                authors = ", ".join(p.authors[:6]) + ("…" if len(p.authors) > 6 else "")
                ui.label(authors).classes("ca-muted").style("font-size:.8rem;")
            if p.abstract:
                with ui.expansion("Abstract").classes("w-full").style("font-size:.85rem;"):
                    ui.label(p.abstract).style(f"color:{INK}; font-size:.85rem; line-height:1.5;")
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

    def _download_csv(self) -> None:
        if not self.result:
            return
        topics = {t.topic_id: t.name for t in self.result.topics}
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["paper_id", "title", "topic", "confidence", "authors", "pdf_url", "url"]
        )
        for p in self.result.relevant_papers:
            writer.writerow(
                [
                    p.paper_id,
                    p.title,
                    topics.get(p.topic_id, ""),
                    f"{p.confidence:.2f}" if p.confidence is not None else "",
                    "; ".join(p.authors),
                    p.pdf_url,
                    p.url,
                ]
            )
        ui.download.content(buf.getvalue().encode("utf-8"), "analysis.csv")


def create_ui() -> None:
    AnalyzerUI().build()
