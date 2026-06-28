"""Console entry point for the Conference Paper Analyzer GUI."""

from __future__ import annotations

import argparse

from nicegui import ui

from .app import create_ui
from .cache import clear_cache, default_cache_dir


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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="conference-analyzer",
        description="Browse, theme-classify, and topic-model conference papers.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="delete cached scrapes and classification results, then exit",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=f"cache directory (default: {default_cache_dir()})",
    )
    parser.add_argument("--host", default="0.0.0.0", help="host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="port to bind (default: 8080)")
    return parser.parse_args(argv)


def main() -> None:
    """Launch the web UI, or run a maintenance command."""
    args = _parse_args()

    if args.clear_cache:
        target, removed = clear_cache(args.cache_dir)
        print(f"Cleared {removed} item(s) from {target}")
        return

    _load_env()
    ui.run(
        title="Conference Paper Analyzer",
        host=args.host,
        port=args.port,
        reload=False,
        favicon="🔬",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
