# Conference Paper Analyzer

A desktop-style web app (built with [NiceGUI](https://nicegui.io)) that:

1. **Browses** papers from the [ACL Anthology](https://aclanthology.org) — or any
   structurally compatible site — for a chosen event (default
   `acl-2026`) and retrieves their abstracts.
2. **Classifies** each paper with an LLM (Claude) against a customizable
   **theme** (default *Agentic AI*), keeping only those whose core contribution
   matches.
3. **Discovers topics** within the selected papers (LLM-based by default, with an
   optional BERTopic backend) and shows, for each topic, how many papers it
   contains and a direct **PDF link** to the full text of every paper.

The interface is deliberately sober and professional: white background, a single
navy accent, clean cards.

![overview](docs/overview.png)

## Quick start

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # Claude/Anthropic works out of the box
export ANTHROPIC_API_KEY=sk-ant-...       # key for your chosen provider (see below)
uv run conference-analyzer                # or: uv run python run.py
```

Then open <http://localhost:8080>.

`uv` provisions the right Python automatically (pinned to 3.13 via
`.python-version`); you don't need to install it yourself.

### LLM providers

The classifier and topic engine work with three providers, chosen in the UI:

| Provider | Install | API key (env var or the in-app field) | Notes |
|----------|---------|----------------------------------------|-------|
| **Anthropic** (default) | included | `ANTHROPIC_API_KEY` | Claude models, native structured output |
| **OpenAI** | `uv sync --extra openai` | `OPENAI_API_KEY` | OpenAI or any OpenAI-compatible base URL |
| **LiteLLM** | `uv sync --extra litellm` | `LITELLM_API_KEY` / `OPENAI_API_KEY` | point **LLM endpoint** at your own LiteLLM URL |

Install both extra providers with `uv sync --extra all`. In the app, pick the
**LLM provider**, set the **Model**, and (for LiteLLM / OpenAI-compatible
servers) the **LLM endpoint**. An **API key** field overrides the env var when
set — handy for a self-hosted endpoint.

> **Note on the default event:** ACL 2026 proceedings may not be published yet.
> The app handles this gracefully and tells you so — try a past event such as
> `acl-2024` to see a full run.

## How it works

| Stage | Module | Notes |
|-------|--------|-------|
| Scrape listing | `conference_analyzer/scraper.py` | Parses the event page; abstracts + authors are fetched per paper and cached on disk. |
| Classify | `conference_analyzer/classifier.py` | Batched, structured-output calls; relevance + confidence + a one-line reason per paper. |
| Topic model | `conference_analyzer/topics.py` | `llm` backend derives a taxonomy and assigns papers; `bertopic` backend optional. |
| Orchestrate | `conference_analyzer/pipeline.py` | Runs the three stages with progress reporting. |
| UI | `conference_analyzer/app.py` | NiceGUI; charts via ECharts; CSV/JSON export. |

## Configuration (in the UI)

- **Anthology base URL** — defaults to `https://aclanthology.org`; change it to
  point at a compatible mirror.
- **Event** — a slug (`acl-2024`, `emnlp-2023`, …) or a full event URL.
- **Theme** — any phrase; defaults to *Agentic AI*.
- **LLM provider** — Anthropic (default), OpenAI, or LiteLLM.
- **Model** — free-text; defaults per provider (e.g. `claude-opus-4-8`,
  `gpt-4o-mini`). Suggestions appear as placeholder text.
- **LLM endpoint / API key** — a custom base URL (LiteLLM or any
  OpenAI-compatible server) and an optional key override.
- **Topic engine** — `LLM` (no extra deps) or `BERTopic` (requires the
  optional `bertopic` install).
- **Max papers**, **target topics**, **minimum confidence** — tuning knobs.

## Features

- Per-topic bar chart of paper counts.
- Expandable abstracts and a per-paper relevance rationale.
- One-click **PDF** links to every paper's full text.
- Export results as a **PPTX** slide deck, **JSON**, or **CSV**.
  The deck (built with `python-pptx`) has a title slide, a papers-per-topic
  chart, and one slide per topic listing its papers with clickable PDF links.
- On-disk caching of scraped data so re-runs are fast (see below).

## Caching

All scraping is cached on disk under `~/.cache/conference_analyzer`:

- the **event listing** is cached per event URL, and
- each paper's **abstract + authors** is cached per paper id.

Because classification and topic modelling are the only theme-dependent stages,
running the analyzer **again with a different theme on the same event reuses the
cached scrape entirely** — no pages are re-downloaded, so only the LLM stages
run. Tick **“Refresh from source”** in the UI (or call
`list_papers(..., force_refresh=True)` / `enrich_abstracts(..., force_refresh=True)`)
to bypass and rebuild the cache.

## Cost

Classification batches ~20 papers per request, so a 150-paper run is a handful
of API calls. Pick a small model (e.g. `claude-haiku-4-5` or `gpt-4o-mini`) to
minimise cost.

## Optional: BERTopic

```bash
uv sync --extra bertopic
```

Then pick **BERTopic** as the topic engine in the UI. It clusters
sentence-embeddings instead of asking the LLM to organise topics.
