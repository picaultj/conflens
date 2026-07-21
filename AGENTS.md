# AGENTS.md

Guidance for coding agents (and humans) working in this repository. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for diagrams.

## What this is

**ConfLens** — a NiceGUI web app that browses conference papers (ACL Anthology,
EMNLP, NAACL, IJCAI, OpenReview / ICLR · NeurIPS, PSCC, and DBLP-indexed venues
such as ISGT Europe), classifies them against a theme with an LLM, discovers
topics, and synthesises per-topic findings. It also
flags near-duplicate titles and offers a fully client-side results view
(live confidence re-threshold, keyword search + highlight, sort/author facets,
save/load a run). Python 3.13, managed with **uv**.

## Setup & run

```bash
uv sync                        # base install (Anthropic, OpenAI, LiteLLM + NiceGUI)
uv sync --extra bertopic       # + BERTopic topic backend (heavy)

cp .env.example .env           # add provider key(s); loaded automatically
uv run conference-analyzer     # NiceGUI GUI on http://localhost:6868
uv run conference-analyzer --clear-cache   # wipe the on-disk cache
```

The GUI (`app.py`) is a thin **NiceGUI** presentation layer; all non-UI logic
lives in `view.py` + `pipeline.py` — put behaviour changes there.

Docker: `docker compose up --build` (see the README).

## Checks before you commit

Run the full gate — CI (GitHub Actions) runs the same three on every push/PR:

```bash
uv run ruff check .        # lint (also `ruff check --fix .` to auto-fix imports)
uv run pytest -q           # test suite — network- and API-free, fast
uv build                   # wheel + sdist build
```

The tests live in `tests/` and are deterministic: parsers run on HTML/JSON
fixtures and the LLM stages use a fake `LLMClient` (a class with a
`structured()` method), so **no network or API keys are needed**. When you add
behaviour, add a test next to the matching `tests/test_*.py`.

Beyond the automated gate, for changes with real runtime surface:

- **Boot the app:** `uv run conference-analyzer`, confirm `GET /` returns 200
  and the logs are clean.
- **Scraper/source changes:** also validate against the live source (counts, a
  sample record's title/authors/abstract). ACL listing pages are large and may
  truncate mid-download — `_robust_get` retries for a complete read; don't cache
  a partial. OpenReview's v2 API may challenge anonymous requests from some IPs —
  set `OPENREVIEW_USERNAME`/`OPENREVIEW_PASSWORD` or `OPENREVIEW_TOKEN` to
  authenticate (see `.env.example`).

Keep changes ASCII-clean and match the surrounding style (dataclasses, targeted
regexes over heavyweight parsers, small focused modules).

## Where things live

| Area | Module |
|------|--------|
| UI (NiceGUI) | `app.py` |
| View logic | `view.py` (filter/sort/highlight/compute_view/exports) |
| Exports | `pptx_export.py`, `bibtex.py` |
| Orchestration | `pipeline.py` (`AnalysisConfig`, `run_analysis`) |
| Sources | `sources.py` (registry + `make_source`; `IJCAISource`, `OpenReviewSource`, `PSCCSource`, `DBLPSource`), `scraper.py` (`AnthologyScraper` — also serves EMNLP/NAACL) |
| LLM providers | `llm.py` (`LLMClient`, `make_client`) |
| Classify / topics | `classifier.py`, `topics.py` |
| Near-duplicate detection | `dedup.py` (`annotate_duplicates`) |
| Cache / models | `cache.py`, `models.py` |

## Conventions

- **All LLM calls go through `llm.py`'s `structured()`** — never call a provider
  SDK directly from `classifier.py` / `topics.py`.
- **Add a conference**: implement `resolve_url` / `list_papers` /
  `enrich_abstracts` in `sources.py`, then register it in `SOURCES`.
- **Add an LLM provider**: add an `LLMClient` subclass + a branch in
  `make_client()`. Everything downstream uses the same interface.
- **Anything expensive should be cached** under `cache.default_cache_dir()`,
  keyed so a re-run with the same inputs is free (see the caching table in
  ARCHITECTURE.md). Respect the `force_refresh` flag.
- **Never hardcode API keys.** Read from env / `.env` / the in-app field.

## LLM / model notes

- Default model is `gpt-5.4` (OpenAI); structured output uses
  `output_config.format` (Anthropic) or JSON-object mode + schema-in-prompt
  (OpenAI/LiteLLM).
- The `effort` parameter is only sent to models that support it (e.g. **not**
  Haiku 4.5 — it 400s). See `_EFFORT_MODELS` in `llm.py`.
- OpenAI/LiteLLM calls fall back gracefully (drop `response_format`, then
  `temperature`) for models/endpoints that reject them.

## Git workflow

- **Do not commit to `main` directly.** Create a feature branch per change and
  open a PR into `main`. Name the branch with a `feat/…` or `fix/…` prefix, and
  **the branch pushed to GitHub (the PR's source) must not contain "claude"** in
  its name (see [CLAUDE.md](CLAUDE.md)).
- **No AI-assistant / Claude Code attribution** anywhere — no `Co-Authored-By`,
  `Claude-Session`, `🤖 Generated with …`, or `claude.ai/code` in commit
  messages or PR descriptions, and don't attribute commit authorship to an
  assistant. See [CLAUDE.md](CLAUDE.md). (The Anthropic *LLM provider* stays —
  it's a feature.)
- `.env`, `.venv`, `__pycache__`, `~/.cache/...` and build artefacts are
  gitignored — never commit them.
- **CI** is one workflow per concern under `.github/workflows/`: `lint.yml`
  (ruff on every commit), `test.yml` (pytest + build), `release.yml`
  (version bump + tag on PR merge). Keep lint and tests green.
- **Releases are automated** (`.github/workflows/release.yml`): merging a PR into
  `main` bumps the version and tags it. Don't bump `version` in `pyproject.toml`
  by hand — control the bump with a `minor`/`major` PR label (default patch).
