# Architecture

ConfLens is a small, single-process web app. A NiceGUI front end drives a
linear pipeline — **browse → classify → topic-model → summarize** — over a
pluggable *source* (which conference) and a pluggable *LLM provider* (which
model). Everything expensive is cached on disk.

## Components

```mermaid
flowchart TB
    RUN["run.py"] --> CLI["cli.py<br/>console script · --clear-cache · .env"]
    CLI --> UI["app.py<br/>NiceGUI UI · charts · exports"]
    UI --> PIPE["pipeline.py<br/>run_analysis(cfg)"]

    PIPE --> REG["sources.py<br/>make_source()"]
    PIPE --> CLS["classifier.py<br/>relevance vs theme"]
    PIPE --> TOP["topics.py<br/>model + summarize"]
    PIPE --> RES["models.py<br/>AnalysisResult"]

    subgraph sources["Paper sources"]
      REG --> ACL["AnthologyScraper<br/>scraper.py"]
      REG --> IJ["IJCAISource"]
    end

    subgraph llm["LLM providers (llm.py · make_client)"]
      AN["Anthropic"]
      OA["OpenAI / compatible"]
      LL["LiteLLM + custom endpoint"]
    end

    CLS --> LLMHUB["llm.py"]
    TOP --> LLMHUB
    LLMHUB --> AN & OA & LL

    UI --> EXP["pptx_export.py<br/>PPTX · JSON · CSV"]
    RES --> EXP

    ACL -. cache .-> CACHE[("cache.py<br/>~/.cache/conference_analyzer")]
    IJ -. cache .-> CACHE
    CLS -. cache .-> CACHE
    TOP -. cache .-> CACHE
```

## Pipeline stages

```mermaid
flowchart LR
    A["Browse<br/>list papers"] --> B["Fetch<br/>abstracts + authors"]
    B --> C["Classify<br/>relevance + confidence"]
    C -->|relevant only| D["Topic model<br/>discover + assign"]
    D --> E["Summarize<br/>description + common findings"]
    E --> F["Display + Export"]
```

Each stage reports progress back to the UI; the whole run executes in a worker
thread so the interface stays responsive.

## A run, end to end

```mermaid
sequenceDiagram
    actor User
    participant UI as UI (app.py)
    participant P as Pipeline
    participant S as Source
    participant C as Classifier
    participant T as Topics
    participant L as LLM provider

    User->>UI: Configure + click Analyze
    UI->>UI: validate (model set, target, endpoint)
    UI->>P: run_analysis(cfg) in a worker thread
    P->>S: list_papers + enrich_abstracts
    Note over S: disk cache per URL / paper id
    S-->>P: papers (title, abstract, authors)
    P->>C: classify_papers(theme)
    C->>L: batched structured calls
    Note over C: cache per (provider+model, theme)
    C-->>P: relevant papers
    P->>T: model_topics (discover + assign)
    T->>L: taxonomy + assignment
    P->>T: summarize_topics
    T->>L: per-topic description + common findings
    Note over T: cache per (provider+model, theme, membership)
    T-->>P: topics
    P-->>UI: AnalysisResult
    UI-->>User: topics · findings · papers · PPTX/JSON/CSV
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Console entry point (`conference-analyzer`); `--clear-cache`, `--host/--port`; loads `.env`. |
| `app.py` | NiceGUI UI: configuration form, input validation, progress, results (ECharts chart, per-topic findings + papers), exports. |
| `pipeline.py` | `AnalysisConfig` + `run_analysis()` orchestrating the stages with a `Progress` object. |
| `sources.py` | Source interface + registry + `make_source()`; `IJCAISource`. |
| `scraper.py` | `AnthologyScraper` (ACL Anthology adapter) + shared HTML helpers. |
| `classifier.py` | Batched, structured-output relevance classification with on-disk cache. |
| `topics.py` | Topic modelling (LLM / BERTopic) **and** per-topic synthesis (`summarize_topics`). |
| `llm.py` | Provider abstraction (`LLMClient`) + `make_client()` for Anthropic / OpenAI / LiteLLM. |
| `pptx_export.py` | Deterministic PowerPoint deck via `python-pptx`. |
| `cache.py` | Cache location + `clear_cache()`. |
| `models.py` | `Paper`, `Topic`, `AnalysisResult` dataclasses. |

## Data model

```mermaid
classDiagram
    class AnalysisResult {
      theme
      event_url
      scanned
      papers
      relevant_papers
      topics
    }
    class Topic {
      topic_id
      name
      description
      findings
      paper_ids
    }
    class Paper {
      paper_id
      title
      url
      pdf_url
      authors
      abstract
      relevant
      confidence
      reason
      topic_id
    }
    AnalysisResult "1" --> "*" Paper
    AnalysisResult "1" --> "*" Topic
    Topic "1" --> "*" Paper : paper_ids
```

## Caching

All caches live under `~/.cache/conference_analyzer` (override with
`--cache-dir`; wipe with `--clear-cache` or the UI's *Refresh from source*).

| Cache | Key | Invalidated by |
|-------|-----|----------------|
| Listing | source page URL | Refresh from source |
| Abstract + authors | paper id | Refresh from source |
| Classification | (provider+model, theme) + paper title/abstract hash | model/theme change, edited abstract, Refresh |
| Topic summary | (provider+model, theme) + topic paper membership | membership change, model/theme change, Refresh |

Because only the theme- and model-dependent stages are keyed on those, changing
the **theme** reuses the scrape, and re-running the **same theme + model** reuses
everything.

## Extension points

- **Add a conference**: implement `resolve_url` / `list_papers` /
  `enrich_abstracts` in `sources.py` and register it in `SOURCES`.
- **Add an LLM provider**: add an `LLMClient` subclass in `llm.py` and a branch
  in `make_client()`. Classification and summarization work through the same
  `structured()` interface, so nothing downstream changes.
- **Swap topic modelling**: `topics.model_topics()` dispatches on a backend
  string (`llm` / `bertopic`).
