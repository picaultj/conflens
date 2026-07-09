"""Lightweight near-duplicate detection over papers, by title similarity.

Dependency-free: exact-normalised-title grouping catches the common case (the
same paper appearing in several tracks / with reformatted punctuation), plus a
bounded fuzzy pass (``difflib`` ratio) within first-token buckets to catch minor
wording differences. No embeddings, so it stays fast and installs nothing.

Each paper in a group of size > 1 (except the representative) gets its
``duplicate_of`` set to the representative's ``paper_id``.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import Paper

_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_WS = re.compile(r"\s+")

_FUZZY_RATIO = 0.92     # normalised-title similarity to treat as a near-duplicate
_BUCKET_CAP = 400       # skip the fuzzy pass for pathologically large buckets


def _normalise(title: str) -> str:
    return _WS.sub(" ", _NON_ALNUM.sub(" ", title.lower())).strip()


class _DSU:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # keep the earlier paper as root


def annotate_duplicates(papers: list[Paper]) -> int:
    """Set ``duplicate_of`` on near-duplicate papers in place.

    Returns the number of duplicate *groups* (clusters of size > 1).
    """
    n = len(papers)
    for p in papers:            # reset any prior annotation
        p.duplicate_of = None
    if n < 2:
        return 0

    norms = [_normalise(p.title) for p in papers]
    dsu = _DSU(n)

    # 1. Exact normalised-title matches.
    exact: dict[str, int] = {}
    for i, key in enumerate(norms):
        if not key:
            continue
        if key in exact:
            dsu.union(exact[key], i)
        else:
            exact[key] = i

    # 2. Bounded fuzzy pass within first-token buckets.
    buckets: dict[str, list[int]] = {}
    for i, key in enumerate(norms):
        if key:
            buckets.setdefault(key.split(" ", 1)[0], []).append(i)
    for members in buckets.values():
        if len(members) < 2 or len(members) > _BUCKET_CAP:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                if dsu.find(i) == dsu.find(j):
                    continue
                if SequenceMatcher(None, norms[i], norms[j]).ratio() >= _FUZZY_RATIO:
                    dsu.union(i, j)

    # 3. Collect clusters; annotate non-representatives.
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)

    dup_groups = 0
    for root, members in groups.items():
        if len(members) < 2:
            continue
        dup_groups += 1
        rep_id = papers[root].paper_id
        for idx in members:
            if idx != root:
                papers[idx].duplicate_of = rep_id
    return dup_groups
