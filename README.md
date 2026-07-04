# ConfLens: a Conference Paper Analyzer

[![CI](https://img.shields.io/github/actions/workflow/status/picaultj/conflens/ci.yml?branch=main&logo=github&label=CI)](https://github.com/picaultj/conflens/actions/workflows/ci.yml)
[![Downloads](https://img.shields.io/github/downloads/picaultj/conflens/total?logo=github&label=downloads)](https://github.com/picaultj/conflens/releases)
[![Release](https://img.shields.io/github/v/release/picaultj/conflens?logo=github&label=release&sort=semver)](https://github.com/picaultj/conflens/releases)
[![Stars](https://img.shields.io/github/stars/picaultj/conflens?logo=github&style=flat)](https://github.com/picaultj/conflens/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/picaultj/conflens?logo=github)](https://github.com/picaultj/conflens/commits/main)
[![Issues](https://img.shields.io/github/issues/picaultj/conflens?logo=github)](https://github.com/picaultj/conflens/issues)
[![License: MPL 2.0](https://img.shields.io/badge/license-MPL%202.0-brightgreen.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/managed%20by-uv-DE5FE9.svg?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Built with NiceGUI](https://img.shields.io/badge/UI-NiceGUI-2b6cb0.svg)](https://nicegui.io)

A desktop-style web app (built with [NiceGUI](https://nicegui.io)) that:

1. **Browses** papers from a chosen **source** and retrieves their abstracts:
   - the [ACL Anthology](https://aclanthology.org) (default; e.g. `acl-2026`),
   - **EMNLP** and **NAACL** proceedings (also on the ACL Anthology; e.g.
     `emnlp-2024`, `naacl-2024`),
   - **IJCAI** accepted-paper pages (e.g. <https://2026.ijcai.org/accepted-papers/>), and
   - **OpenReview** venues — **ICLR / NeurIPS** and more — via the public JSON API,
     by venue id (e.g. `ICLR.cc/2024/Conference`, `NeurIPS.cc/2024/Conference`).

   The scraper is pluggable — adding another conference is one adapter in
   `conference_analyzer/sources.py`.
2. **Classifies** each paper with an LLM against a customizable
   **theme** (default *Agentic AI*), keeping only those whose core contribution
   matches.
3. **Discovers topics** within the selected papers (LLM-based by default, with an
   optional BERTopic backend) and shows, for each topic, how many papers it
   contains and a direct **PDF link** to the full text of every paper.


![overview](docs/overview.png)

## Contents

- [Quick start](#quick-start) · [Run with Docker](#run-with-docker) · [LLM providers](#llm-providers)
- [Architecture](#architecture) · [How it works](#how-it-works)
- [Configuration](#configuration-in-the-ui) · [Features](#features)
- [Caching](#caching) · [Cost](#cost) · [BERTopic](#optional-bertopic)
- [Development](#development)

## Quick start

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # Claude/Anthropic works out of the box
cp .env.example .env                       # then fill in your provider key(s)
uv run conference-analyzer                # or: uv run python run.py
```

Keys are read from `.env` (loaded automatically) or the process environment;
you can also paste a key into the app's **API key** field at runtime.

Then open <http://localhost:8080>.

`uv` provisions the right Python automatically (pinned to 3.13 via
`.python-version`); you don't need to install it yourself.

### Run with Docker

```bash
cp .env.example .env          # fill in your provider key(s)
docker compose up --build     # → http://localhost:8080
```

The image is built with `uv` on Python 3.13 and includes the OpenAI + LiteLLM
providers. A named volume persists the cache (scrapes + classifications) across
restarts. To clear the cache in a container:

```bash
docker compose run --rm app --clear-cache
```

Plain Docker (no compose) works too:

```bash
docker build -t conference-analyzer .
docker run --rm -p 8080:8080 --env-file .env \
  -v conf-cache:/home/app/.cache/conference_analyzer conference-analyzer
```

To also build the optional BERTopic backend into the image:
`docker build --build-arg EXTRAS="--extra all --extra bertopic" -t conference-analyzer .`

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

## Architecture

A NiceGUI front end drives a linear pipeline — **browse → classify →
topic-model → summarize** — over a pluggable *source* (which conference) and a
pluggable *LLM provider* (which model), with every expensive step cached on disk.

```mermaid
flowchart LR
    UI["NiceGUI UI<br/>app.py"] --> PIPE["pipeline.py"]
    PIPE --> SRC["sources.py<br/>ACL · EMNLP · NAACL · IJCAI · OpenReview"]
    PIPE --> CLS["classifier.py"]
    PIPE --> TOP["topics.py<br/>model + summarize"]
    CLS --> LLM["llm.py<br/>Anthropic · OpenAI · LiteLLM"]
    TOP --> LLM
    PIPE --> RES["AnalysisResult"] --> EXP["PPTX · BibTeX · JSON · CSV"]
    SRC -. cache .-> CACHE[("~/.cache/conference_analyzer")]
    CLS -. cache .-> CACHE
    TOP -. cache .-> CACHE
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for component, sequence,
data-model and caching diagrams, plus extension points.

## How it works

| Stage | Module | Notes |
|-------|--------|-------|
| Sources | `conference_analyzer/sources.py` | Pluggable adapters (ACL Anthology, EMNLP, NAACL, IJCAI, OpenReview) behind one interface; registry + factory. |
| Scrape listing | `conference_analyzer/scraper.py` | ACL Anthology adapter: parses the event page; abstracts + authors fetched per paper and cached. |
| Near-duplicates | `conference_analyzer/dedup.py` | Flags near-identical titles (union-find + `difflib`); dependency-free. |
| Classify | `conference_analyzer/classifier.py` | Batched, structured-output calls; relevance + confidence + a one-line reason per paper (cached). |
| Topic model | `conference_analyzer/topics.py` | `llm` backend derives a taxonomy and assigns papers (primary + optional secondary topic); `bertopic` backend optional. |
| Summarize | `conference_analyzer/topics.py` | Per-topic description + 5–10 common findings across the topic's papers (cached). |
| LLM providers | `conference_analyzer/llm.py` | One `structured()` interface over Anthropic / OpenAI / LiteLLM. |
| Orchestrate | `conference_analyzer/pipeline.py` | Runs the stages with progress reporting and cooperative cancel. |
| UI / exports | `conference_analyzer/app.py`, `pptx_export.py`, `bibtex.py` | NiceGUI; ECharts chart; interactive results view; PPTX / BibTeX / JSON / CSV export. |

## Configuration (in the UI)

- **Source** — `ACL Anthology`, `EMNLP (ACL Anthology)`, `NAACL (ACL Anthology)`,
  `IJCAI`, or `OpenReview (ICLR / NeurIPS)`. Switching prefills the base URL and
  target below and relabels them. (ACL Anthology, EMNLP and NAACL share one
  adapter — any of them accepts any Anthology event slug, e.g. `acl-2024`,
  `emnlp-2023`, `naacl-2024`.)
- **Base URL** — the site/API root (e.g. `https://aclanthology.org`,
  `https://2026.ijcai.org`, `https://api2.openreview.net`); change it to point at
  a mirror, another year, or the v1 API host (`https://api.openreview.net`).
- **Event / target** — for ACL, a slug (`acl-2024`, `emnlp-2023`, …) or full
  event URL; for IJCAI, the accepted-papers path or full URL; for OpenReview, the
  **venue id** (`ICLR.cc/2024/Conference`, `NeurIPS.cc/2024/Conference`) or a
  venue group URL.

  > **OpenReview auth** — recent (API v2) venues challenge anonymous requests
  > from some networks. If a run returns an auth error, set `OPENREVIEW_TOKEN`,
  > or `OPENREVIEW_USERNAME` + `OPENREVIEW_PASSWORD` (see `.env.example`); the
  > adapter logs in and authenticates automatically. Older venues work
  > anonymously.
- **Theme** — any phrase; defaults to *Agentic AI*.
- **Theme definition** — optional free text clarifying what the theme includes
  or excludes (e.g. *"tool-using LLM agents; exclude pure RL"*). It is threaded
  into classification, topic discovery, and summaries to sharpen relevance, and
  is part of the classification cache key.
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
- For each topic, an LLM-generated **description** and **5–10 common findings**
  (synthesised across the topic's papers, not paper-specific), shown above the
  paper list.
- **Multi-topic assignment** — a paper can belong to a primary topic and, when it
  genuinely spans two areas, a secondary one; secondary memberships show an
  *"Also in: …"* line, and the CSV export lists every assigned topic.
- **Near-duplicate detection** — papers with near-identical titles (e.g. a
  preprint and its camera-ready) are clustered with a dependency-free fuzzy match
  and flagged with a *near-dup* badge naming the representative; the count of
  groups appears in the summary and a `duplicate_of` column is added to the CSV.
- A **keyword search** that filters the papers by their title or abstract —
  comma-separated keywords (each may contain spaces), matched with AND. Matched
  keywords are **highlighted** in the title and abstract, topics with no match
  are hidden, and a **"Search all topics"** toggle flattens every match into one
  ranked list (a multi-topic paper appears once, tagged with its topics).
- A **live confidence slider** in the results view: because the raw model
  judgement is cached, re-thresholding re-filters the papers, counts, and chart
  instantly — no re-run.
- **Sort** papers by confidence, title, or year, and **filter by author** —
  all applied live on top of the search and confidence filters.
- **Save / load a run** — the JSON export is a complete snapshot; use *"Load
  saved run"* to reopen it later and browse, search, and re-threshold without
  re-analysing.
- Expandable abstracts and a per-paper relevance rationale.
- One-click **PDF** links to every paper's full text.
- A **Cancel** button and elapsed-time readout for long runs; LLM calls
  automatically retry transient errors (rate limits / 5xx / timeouts).
- Export results as a **PPTX** slide deck, **BibTeX**, **JSON**, or **CSV**.
  The deck (built with `python-pptx`) has a title slide, a papers-per-topic
  chart, and per topic an overview slide (description + common findings) plus
  slides listing its papers with clickable PDF links.
- On-disk caching of scraped data so re-runs are fast (see below).

## Caching

Everything expensive is cached on disk under `~/.cache/conference_analyzer`:

- the **event listing** is cached per event/page URL,
- each paper's **abstract + authors** is cached per paper id, and
- **classification results** are cached per *(provider + model, theme)* and keyed
  by each paper's title+abstract hash. The raw model judgement is stored, so the
  **minimum-confidence** threshold is applied at read time — adjusting it never
  triggers a re-call.
- **topic summaries** (description + common findings) are cached per
  *(provider + model, theme)* and keyed by the topic's exact paper membership.

So re-running with a **different theme** reuses the cached scrape (only
classification re-runs), and **re-running the same theme + model is essentially
free**. Changing the model or a paper's abstract re-classifies only what's
affected. Tick **“Refresh from source”** in the UI to bypass every cache and
rebuild from scratch.

To wipe the cache from the command line:

```bash
uv run conference-analyzer --clear-cache          # default ~/.cache/conference_analyzer
uv run conference-analyzer --clear-cache --cache-dir /path/to/cache
```

Other flags: `--host`, `--port` (run `conference-analyzer --help`).

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

## Development

```bash
uv sync              # installs the dev group (pytest + ruff) too
uv run pytest        # run the test suite
uv run ruff check .  # lint
uv build             # build the wheel/sdist
```

CI (GitHub Actions) runs ruff + pytest + build on every push and PR. The tests
are network- and API-free (parsers run on HTML fixtures; the LLM stages use a
fake client), so they're fast and deterministic.

