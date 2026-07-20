"""Gradio front-end for ConfLens — feature-parity with the NiceGUI app.

This is the front-end deployed to Hugging Face Spaces (Gradio SDK Spaces are
free). It shares all non-UI logic with the NiceGUI app via :mod:`conflens.view`
and :mod:`conflens.pipeline`, so keep behavioural changes in those shared modules
and mirror only the *presentation* here and in ``app.py``.
"""

from __future__ import annotations

import html
import os
import tempfile
import threading
import time
from typing import Optional

import gradio as gr

from . import view
from .cache import default_cache_dir
from .llm import DEFAULT_MODELS, MODEL_SUGGESTIONS, PROVIDERS, env_key_for
from .models import AnalysisResult
from .pipeline import AnalysisConfig, Progress, run_analysis
from .sources import SOURCES
from .view import TOPIC_COLORS

_CACHE_DIR = default_cache_dir()

# Sober palette (mirrors app.py).
PRIMARY = "#1f4e79"
INK = "#1a202c"
MUTED = "#64748b"
LINE = "#e2e8f0"

_CSS = """
.conflens mark { background:#fde68a; color:inherit; padding:0 1px; border-radius:2px; }
.conflens .ca-card { border:1px solid %s; border-radius:10px; background:#fff;
  padding:14px 18px; margin-bottom:14px; }
.conflens a.ca-title { color:%s; font-weight:600; text-decoration:none; }
.conflens a.ca-title:hover { text-decoration:underline; }
.conflens .ca-badge { display:inline-block; color:#fff; border-radius:6px;
  padding:1px 7px; font-size:.72rem; font-weight:600; text-decoration:none; margin-left:4px; }
.conflens .muted { color:%s; font-size:.82rem; }
""" % (LINE, PRIMARY, MUTED)


# --------------------------------------------------------------------------- #
# HTML rendering (presentation only; all logic comes from view.compute_view)
# --------------------------------------------------------------------------- #
def _chart_html(names: list[str], counts: list[int]) -> str:
    if not names:
        return ""
    top = max(counts) or 1
    rows = []
    for i, (name, c) in enumerate(zip(names, counts)):
        color = TOPIC_COLORS[i % len(TOPIC_COLORS)]
        width = max(2, round(100 * c / top))
        rows.append(
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
            f'<div style="flex:0 0 200px;text-align:right;font-size:.8rem;color:{INK};'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{html.escape(name)}</div>'
            f'<div style="flex:1;background:#f1f5f9;border-radius:4px;">'
            f'<div style="width:{width}%;background:{color};height:16px;border-radius:4px;"></div></div>'
            f'<div style="flex:0 0 30px;font-size:.8rem;color:{MUTED};">{c}</div></div>'
        )
    return (
        '<div class="ca-card"><div style="font-weight:700;color:%s;margin-bottom:6px;">'
        "Papers per topic</div>%s</div>" % (INK, "".join(rows))
    )


def _paper_html(p, kws: list[str], also_in: list[str], dup: Optional[str], also_label: str) -> str:
    if kws:
        title = view.highlight(p.title, kws)
    else:
        title = html.escape(p.title)
    if p.url:
        title_html = (
            f'<a href="{html.escape(p.url)}" target="_blank" rel="noopener" '
            f'class="ca-title">{title}</a>'
        )
    else:
        title_html = f'<span style="color:{INK};font-weight:600;">{title}</span>'

    badges = ""
    if dup:
        badges += (
            f'<span class="ca-badge" style="background:#b7791f;" '
            f'title="Near-duplicate of: {html.escape(dup)}">near-dup</span>'
        )
    if p.confidence is not None:
        badges += (
            f'<span class="ca-badge" style="background:{MUTED};" '
            f'title="Relevance confidence">{p.confidence:.0%}</span>'
        )
    if p.pdf_url:
        badges += (
            f'<a class="ca-badge" style="background:#2b6cb0;" '
            f'href="{html.escape(p.pdf_url)}" target="_blank" rel="noopener">PDF</a>'
        )

    parts = [
        f'<div style="padding:8px 0;border-top:1px solid {LINE};">',
        f'<div>{title_html}{badges}</div>',
    ]
    if p.authors:
        auth = ", ".join(p.authors[:6]) + ("…" if len(p.authors) > 6 else "")
        parts.append(f'<div class="muted">{html.escape(auth)}</div>')
    if also_in:
        parts.append(
            f'<div class="muted">{html.escape(also_label)}'
            f'{html.escape(", ".join(also_in))}</div>'
        )
    if p.abstract:
        body = view.highlight(p.abstract, kws) if kws else html.escape(p.abstract)
        parts.append(
            f'<details style="margin-top:2px;"><summary class="muted" '
            f'style="cursor:pointer;">Abstract</summary>'
            f'<div style="color:{INK};font-size:.85rem;line-height:1.5;margin-top:4px;">{body}</div>'
            "</details>"
        )
    if p.reason:
        parts.append(f'<div class="muted" style="font-style:italic;">Why: {html.escape(p.reason)}</div>')
    parts.append("</div>")
    return "".join(parts)


def _render_html(result: AnalysisResult, min_conf, query, author, sort, is_global) -> tuple[str, str]:
    """Return (results_html, status_text) for the current filters."""
    if result is None:
        return "", ""
    kws = view.keywords(query or "")
    vd = view.compute_view(
        result, min_conf=float(min_conf or 0), query=query or "",
        author=author or "", sort=sort or "confidence",
    )
    blocks = [_chart_html(vd.names, vd.counts)]

    if is_global:
        papers = vd.flat
        card = [
            '<div class="ca-card">',
            f'<div class="muted" style="font-weight:600;">All matching papers ({len(papers)})</div>',
        ]
        if not papers:
            card.append('<div class="muted">No papers match the current filters.</div>')
        for p in papers:
            card.append(_paper_html(
                p, kws,
                [vd.topic_name[tid] for tid in p.topic_ids if tid in vd.topic_name],
                view.dup_title(p, vd.all_by_id), "Topics: ",
            ))
        card.append("</div>")
        blocks.append("".join(card))
        shown_papers, shown_topics = len(papers), (1 if papers else 0)
    else:
        shown_papers = shown_topics = 0
        for tv in vd.grouped:
            t, papers = tv.topic, tv.papers
            shown_papers += len(papers)
            shown_topics += 1
            color = TOPIC_COLORS[t.topic_id % len(TOPIC_COLORS)]
            badge = (
                f"{len(papers)} of {t.count}" if len(papers) != t.count
                else f"{t.count} paper{'s' if t.count != 1 else ''}"
            )
            card = [
                '<div class="ca-card">',
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<span style="width:10px;height:10px;border-radius:50%;background:{color};'
                'display:inline-block;"></span>'
                f'<span style="font-weight:700;color:{INK};">{html.escape(t.name)}</span>'
                f'<span class="ca-badge" style="background:{color};">{badge}</span></div>',
            ]
            if t.description:
                card.append(
                    f'<div style="color:{INK};font-size:.9rem;line-height:1.5;margin-top:6px;">'
                    f'{html.escape(t.description)}</div>'
                )
            if t.findings:
                items = "".join(f"<li>{html.escape(f)}</li>" for f in t.findings)
                card.append(
                    f'<div style="background:#f8fafc;border:1px solid {LINE};border-radius:8px;'
                    'padding:8px 14px;margin-top:8px;">'
                    f'<div style="font-weight:700;color:{PRIMARY};font-size:.75rem;'
                    'text-transform:uppercase;letter-spacing:.04em;">Main findings across this topic</div>'
                    f'<ul style="margin:6px 0 0 0;font-size:.85rem;color:{INK};">{items}</ul></div>'
                )
            card.append(f'<div class="muted" style="font-weight:600;margin-top:8px;">Papers ({len(papers)})</div>')
            for p in papers:
                card.append(_paper_html(
                    p, kws, view.also_in(p, t.topic_id, vd.topic_name),
                    view.dup_title(p, vd.all_by_id), "Also in: ",
                ))
            card.append("</div>")
            blocks.append("".join(card))
        if shown_topics == 0:
            blocks.append('<div class="ca-card muted">No papers match the current filters.</div>')

    scope = "in one list" if is_global else f"· {shown_topics} of {vd.total_topics} topics"
    status = (
        f"Show ≥ {float(min_conf or 0):.2f} confidence — "
        f"{shown_papers} of {vd.total_relevant} papers {scope}"
    )
    return f'<div class="conflens">{"".join(blocks)}</div>', status


def _summary_md(result: AnalysisResult) -> str:
    if result is None:
        return ""
    bits = [
        f"**{result.scanned}** scanned",
        f"**{len(result.relevant_papers)}** relevant to *{result.theme}*",
        f"**{len(result.topics)}** topics",
    ]
    if result.duplicate_groups:
        bits.append(f"**{result.duplicate_groups}** near-duplicate groups")
    link = f"  ·  [source]({result.event_url})" if result.event_url else ""
    return "  ·  ".join(bits) + link


def _provider_hint(provider: str, suggestions: str) -> str:
    env_var = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "litellm": "LITELLM_API_KEY / OPENAI_API_KEY",
    }.get(provider, "")
    parts = []
    if env_key_for(provider):
        parts.append(f"Using `{env_var}` from the environment.")
    else:
        parts.append(f"No `{env_var}` found — set it or fill the API key field.")
    if provider == "litellm":
        parts.append("LiteLLM: set the LLM endpoint.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Export helpers (write bytes to a temp file for DownloadButton)
# --------------------------------------------------------------------------- #
def _write_temp(data: bytes, name: str) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="conflens_"), name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _dl_json(result):
    if result is None:
        raise gr.Error("Run or load an analysis first.")
    return _write_temp(view.json_bytes(result), "analysis.json")


def _dl_csv(result):
    if result is None:
        raise gr.Error("Run or load an analysis first.")
    return _write_temp(view.csv_bytes(result), "analysis.csv")


def _dl_bibtex(result):
    if result is None:
        raise gr.Error("Run or load an analysis first.")
    from .bibtex import build_bibtex

    return _write_temp(build_bibtex(result).encode("utf-8"), "papers.bib")


def _dl_pptx(result):
    if result is None:
        raise gr.Error("Run or load an analysis first.")
    from .pptx_export import build_pptx

    try:
        data = build_pptx(result)
    except Exception as e:  # missing dependency / render failure
        raise gr.Error(str(e))
    return _write_temp(data, "analysis.pptx")


# --------------------------------------------------------------------------- #
# Analyze / load lifecycle
# --------------------------------------------------------------------------- #
def _validate(model, event, provider, llm_base_url) -> Optional[str]:
    if not (model or "").strip():
        return "Please set a model before running."
    if not (event or "").strip():
        return "Please set the event / target."
    if provider == "litellm" and not (llm_base_url or "").strip():
        return "LiteLLM needs an LLM endpoint."
    return None


def _populate(result: AnalysisResult):
    """Outputs tuple shared by analyze() and load() to fill the results panel."""
    html_, status = _render_html(result, result.min_confidence, "", "", "confidence", False)
    return (
        result,                                             # result_state
        _summary_md(result),                                # summary_md
        html_,                                              # results_html
        status,                                             # status_md
        gr.update(choices=view.author_choices(result), value=None),  # author_dd
        gr.update(value=result.min_confidence),             # conf_slider
        gr.update(value=""),                                # search_box
        gr.update(value="confidence"),                      # sort_dd
        gr.update(value=False),                             # global_chk
        gr.update(visible=True),                            # results_group
    )


def analyze(
    source, base_url, event, theme, theme_def, provider, model, backend,
    llm_base_url, api_key, max_papers, n_topics, min_conf, refresh,
    progress=gr.Progress(),
):
    err = _validate(model, event, provider, llm_base_url)
    if err:
        raise gr.Error(err)
    cfg = AnalysisConfig(
        source=source,
        base_url=(base_url or "").strip(),
        event=(event or "").strip(),
        theme=(theme or "").strip() or "Agentic AI",
        theme_definition=(theme_def or "").strip(),
        provider=provider,
        model=(model or "").strip(),
        llm_base_url=(llm_base_url or "").strip(),
        api_key=(api_key or "").strip(),
        max_papers=int(max_papers or 150),
        n_topics=int(n_topics or 8),
        min_confidence=float(min_conf),
        topic_backend=backend,
        refresh=bool(refresh),
    )
    prog = Progress()
    box: dict = {}

    def work():
        try:
            box["r"] = run_analysis(cfg, prog, _CACHE_DIR)
        except Exception as e:  # surface to the user
            prog.error = str(e)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    progress(0.0, desc="Starting…")
    while th.is_alive():
        progress(min(prog.fraction, 0.99), desc=prog.message or "Working…")
        time.sleep(0.25)
    th.join()
    if prog.error:
        raise gr.Error(prog.error)
    progress(1.0, desc="Done")
    result = box.get("r")
    if result is None or (not result.relevant_papers and not result.topics):
        empty = AnalysisResult(theme=cfg.theme, event_url=result.event_url if result else "")
        return (
            empty, _summary_md(result) if result else "",
            '<div class="conflens"><div class="ca-card muted">'
            "No matching papers were found for this theme.</div></div>",
            "", gr.update(choices=[], value=None), gr.update(value=cfg.min_confidence),
            gr.update(value=""), gr.update(value="confidence"), gr.update(value=False),
            gr.update(visible=True),
        )
    return _populate(result)


def load_run(file):
    if not file:
        return (None, "", "", "", gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update(visible=False))
    try:
        with open(file, "rb") as fh:
            import json
            result = AnalysisResult.from_dict(json.loads(fh.read().decode("utf-8")))
    except Exception as e:
        raise gr.Error(f"Could not load run: {e}")
    if not result.relevant_papers and not result.topics:
        raise gr.Error("That file doesn't look like a saved analysis run.")
    return _populate(result)


def rerender(result, min_conf, query, author, sort, is_global):
    if result is None:
        return "", ""
    return _render_html(result, min_conf, query, author, sort, is_global)


# --------------------------------------------------------------------------- #
# Blocks layout
# --------------------------------------------------------------------------- #
def build_demo() -> "gr.Blocks":
    default_provider = "litellm"
    with gr.Blocks(title="ConfLens — Conference Paper Analyzer") as demo:
        # Inject CSS via a <style> block so it applies regardless of who calls
        # launch() (Hugging Face launches the Space itself).
        gr.HTML(f"<style>{_CSS}</style>")
        gr.Markdown("## 🔎 ConfLens — Conference Paper Analyzer\nBrowse · classify by theme · discover topics")
        result_state = gr.State(None)

        with gr.Accordion("Configuration", open=True):
            with gr.Row():
                source_dd = gr.Dropdown(
                    choices=[(v["label"], k) for k, v in SOURCES.items()],
                    value="aclanthology", label="Source",
                )
                base_url_tb = gr.Textbox(
                    value=SOURCES["aclanthology"]["base"],
                    label=SOURCES["aclanthology"]["base_label"],
                )
                event_tb = gr.Textbox(
                    value=SOURCES["aclanthology"]["target"],
                    label=SOURCES["aclanthology"]["target_label"],
                )
            with gr.Row():
                theme_tb = gr.Textbox(value="Agentic AI", label="Theme")
                provider_dd = gr.Dropdown(choices=PROVIDERS, value=default_provider, label="LLM provider")
                model_tb = gr.Textbox(value=DEFAULT_MODELS[default_provider], label="Model")
                backend_dd = gr.Dropdown(
                    choices=[("LLM topics", "llm"), ("BERTopic", "bertopic")],
                    value="llm", label="Topic engine",
                )
            theme_def_tb = gr.Textbox(
                label="Theme definition (optional)",
                placeholder="Clarify what counts as this theme — what to include / exclude",
            )
            with gr.Row():
                llm_base_url_tb = gr.Textbox(
                    value=os.environ.get("OPENAI_BASE_URL", ""),
                    label="LLM endpoint (LiteLLM / OpenAI-compatible)",
                )
                api_key_tb = gr.Textbox(label="API key (optional — overrides env var)", type="password")
            key_hint_md = gr.Markdown(_provider_hint(default_provider, ""))
            with gr.Row():
                max_papers_num = gr.Number(value=150, precision=0, label="Max papers to scan")
                n_topics_num = gr.Number(value=8, precision=0, label="Target number of topics")
                min_conf_num = gr.Slider(0, 1, value=0.5, step=0.05, label="Minimum confidence")
            with gr.Row():
                refresh_chk = gr.Checkbox(value=False, label="Refresh from source (ignore cache)")
                load_file = gr.File(label="Load saved run (.json)", file_types=[".json"])
            analyze_btn = gr.Button("Analyze", variant="primary")

        with gr.Group(visible=False) as results_group:
            summary_md = gr.Markdown()
            with gr.Row():
                dl_pptx_btn = gr.DownloadButton("PPTX", size="sm")
                dl_json_btn = gr.DownloadButton("JSON", size="sm")
                dl_csv_btn = gr.DownloadButton("CSV", size="sm")
                dl_bib_btn = gr.DownloadButton("BibTeX", size="sm")
            with gr.Row():
                search_box = gr.Textbox(
                    label="Filter by keywords (comma-separated; each may contain spaces)",
                    scale=3,
                )
                sort_dd = gr.Dropdown(
                    choices=[("Confidence", "confidence"), ("Title", "title"), ("Year", "year")],
                    value="confidence", label="Sort by", scale=1,
                )
            with gr.Row():
                author_dd = gr.Dropdown(choices=[], value=None, label="Filter by author",
                                        allow_custom_value=True, scale=2)
                conf_slider = gr.Slider(0, 1, value=0.5, step=0.05, label="Show ≥ confidence", scale=2)
                global_chk = gr.Checkbox(value=False, label="Search all topics", scale=1)
            status_md = gr.Markdown()
            results_html = gr.HTML()

        # -- wiring --------------------------------------------------------- #
        def on_source(source):
            cfg = SOURCES.get(source, {})
            return (
                gr.update(value=cfg.get("base", ""), label=cfg.get("base_label", "Base URL")),
                gr.update(value=cfg.get("target", ""), label=cfg.get("target_label", "Target")),
            )

        source_dd.change(on_source, [source_dd], [base_url_tb, event_tb])

        def on_provider(provider):
            sugg = ", ".join(MODEL_SUGGESTIONS.get(provider, []))
            return gr.update(value=DEFAULT_MODELS.get(provider, ""), info=f"e.g. {sugg}"), \
                _provider_hint(provider, sugg)

        provider_dd.change(on_provider, [provider_dd], [model_tb, key_hint_md])

        analyze_outputs = [
            result_state, summary_md, results_html, status_md, author_dd,
            conf_slider, search_box, sort_dd, global_chk, results_group,
        ]
        analyze_btn.click(
            analyze,
            [source_dd, base_url_tb, event_tb, theme_tb, theme_def_tb, provider_dd,
             model_tb, backend_dd, llm_base_url_tb, api_key_tb, max_papers_num,
             n_topics_num, min_conf_num, refresh_chk],
            analyze_outputs,
        )
        load_file.change(load_run, [load_file], analyze_outputs)

        view_inputs = [result_state, conf_slider, search_box, author_dd, sort_dd, global_chk]
        for ctrl in (conf_slider, search_box, author_dd, sort_dd, global_chk):
            ctrl.change(rerender, view_inputs, [results_html, status_md])

        dl_json_btn.click(_dl_json, [result_state], [dl_json_btn])
        dl_csv_btn.click(_dl_csv, [result_state], [dl_csv_btn])
        dl_bib_btn.click(_dl_bibtex, [result_state], [dl_bib_btn])
        dl_pptx_btn.click(_dl_pptx, [result_state], [dl_pptx_btn])

    return demo


def main() -> None:
    """Launch the Gradio app (console script: ``conflens-gradio``)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    port = int(os.environ.get("PORT", "7860"))
    build_demo().launch(server_name="0.0.0.0", server_port=port)


if __name__ == "__main__":
    main()
