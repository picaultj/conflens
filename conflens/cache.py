"""Shared on-disk cache location and a helper to clear it.

The cache holds scraped listings, per-paper abstracts/authors, and classification
results — all under one directory so it can be wiped in one go.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional


def default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "conflens")


def clear_cache(cache_dir: Optional[str] = None) -> tuple[str, int]:
    """Delete everything inside the cache directory.

    Returns ``(cache_dir, items_removed)``. The directory itself is kept.
    """
    target = cache_dir or default_cache_dir()
    removed = 0
    if os.path.isdir(target):
        for name in os.listdir(target):
            path = os.path.join(target, name)
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed += 1
            except OSError:
                pass
    return target, removed
