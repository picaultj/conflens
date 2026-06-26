"""Console entry point for the Conference Paper Analyzer GUI."""

from __future__ import annotations

from nicegui import ui

from .app import create_ui


@ui.page("/")
def _index() -> None:
    create_ui()


def _load_env() -> None:
    """Load variables from a local .env file, if present."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is a dependency, but stay defensive
        return
    load_dotenv()


def main() -> None:
    """Launch the web UI (used by the ``conference-analyzer`` script)."""
    _load_env()
    ui.run(
        title="Conference Paper Analyzer",
        host="0.0.0.0",
        port=8080,
        reload=False,
        favicon="🔬",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
