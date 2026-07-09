"""Convenience launcher: ``uv run python run.py`` (or ``python run.py``).

Equivalent to the installed ``conference-analyzer`` console script.
Open http://localhost:6868 once it is running.
"""

from conflens.cli import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
