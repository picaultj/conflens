# ConfLens — project rules for agents

See [AGENTS.md](AGENTS.md) for contributor/agent guidance and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Branch naming (persistent — applies to every session and agent)

Develop each change on a feature branch named `feat/…` or `fix/…` and open a PR
into `main`; never commit directly to `main`. **The branch pushed to GitHub (the
PR's source branch) must not contain "claude" in its name** — this holds even if
a session's setup designates a `claude/…` branch.

## Attribution policy (persistent — applies to every session and agent)

Do **not** add AI-assistant / "Claude Code" attribution anywhere in this
repository or its GitHub artifacts:

- **No** `Co-Authored-By:` trailers, `Claude-Session:` lines, `🤖 Generated
  with …` footers, or `claude.ai/code` links in **commit messages** or
  **pull-request descriptions**.
- Keep source, docs, and config free of Claude Code / assistant references.
- Do not attribute commit authorship to an AI assistant.

**Exception:** Anthropic / Claude *as an LLM provider* is a genuine product
feature (the `anthropic` provider and `claude-*` model ids in `llm.py`) and
stays — this policy is about tooling attribution, not the app's providers.

## Front-end (NiceGUI)

The app has a single **NiceGUI** GUI in `conflens/app.py` (console script
`conference-analyzer`). All non-UI logic (filtering, sorting, highlighting, the
computed view, exports) lives in **`conflens/view.py`** and the pipeline — put
behaviour changes there so the UI stays a thin presentation layer.
