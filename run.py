"""Entry point for the Conference Paper Analyzer GUI.

Run with:  python run.py
Then open http://localhost:8080 in your browser.
"""

from nicegui import ui

from conference_analyzer.app import create_ui


@ui.page("/")
def index() -> None:
    create_ui()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="Conference Paper Analyzer",
        host="0.0.0.0",
        port=8080,
        reload=False,
        favicon="🔬",
    )
