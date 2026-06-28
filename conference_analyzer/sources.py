"""Pluggable paper sources.

Each source exposes the same small interface so the pipeline is site-agnostic:

* ``resolve_url(target) -> str``        — turn a slug/path/URL into a full URL
* ``list_papers(target, force_refresh)``— return papers (titles, links, …)
* ``enrich_abstracts(papers, …)``       — fill abstracts/authors if not already present

Two sources ship today:

* **aclanthology** — the ACL Anthology (:class:`~.scraper.AnthologyScraper`); a
  listing page plus per-paper abstract pages.
* **ijcai** — IJCAI accepted-paper pages (e.g. ``2026.ijcai.org/accepted-papers``),
  where every paper's title, authors, abstract and keywords live on one page.

Adding another conference is a matter of writing one more adapter and
registering it in :data:`SOURCES`.
"""

from __future__ import annotations

import hashlib
import html
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from .models import Paper
from .scraper import AnthologyScraper, _clean

_UA = "conflens/0.1 (+https://github.com/picaultj/conflens)"


def _robust_get(url: str, timeout: int = 120) -> str:
    """Fetch a URL, retrying for a complete read before accepting a partial."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last_err: Optional[Exception] = None
    partial_best = ""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except http.client.IncompleteRead as e:
            chunk = e.partial.decode("utf-8", "replace")
            if len(chunk) > len(partial_best):
                partial_best = chunk
            last_err = e
            time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    if partial_best:
        return partial_best
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


# ---------------------------------------------------------------------------
# IJCAI accepted-papers source
# ---------------------------------------------------------------------------
_IJ_ITEM = re.compile(r'<li class="ij-paper".*?</li>', re.IGNORECASE | re.DOTALL)
_IJ_PID = re.compile(
    r'class="ij-pid"[^>]*>\s*#?\s*([A-Za-z0-9][A-Za-z0-9\-]*)', re.IGNORECASE
)
_IJ_TITLE = re.compile(r'class="ij-ptitle"[^>]*>(.*?)</h3>', re.IGNORECASE | re.DOTALL)
_IJ_AUTHOR = re.compile(r'class="ij-author"[^>]*>(.*?)</span>', re.IGNORECASE | re.DOTALL)
_IJ_ABSTRACT = re.compile(
    r'class="ij-abstract"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL
)
_IJ_KW = re.compile(r'class="ij-kw"\s+title="([^"]+)"', re.IGNORECASE)
_YEAR = re.compile(r"(20\d{2})")


class IJCAISource:
    """Parse an IJCAI accepted-papers page (single page, abstracts inline)."""

    name = "ijcai"

    def __init__(
        self,
        base_url: str = "https://2026.ijcai.org",
        cache_dir: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or "https://2026.ijcai.org").rstrip("/")
        self.timeout = timeout
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "conference_analyzer"
        )
        os.makedirs(self.cache_dir, exist_ok=True)

    def resolve_url(self, target: str) -> str:
        target = (target or "accepted-papers").strip()
        if target.startswith("http://") or target.startswith("https://"):
            return target
        return f"{self.base_url}/{target.strip('/')}/"

    def _cache_path(self, url: str) -> str:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"ijcai_{digest}.json")

    def list_papers(self, target: str, force_refresh: bool = False) -> list[Paper]:
        url = self.resolve_url(target)
        cache = self._cache_path(url)
        if not force_refresh and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return [Paper(**p) for p in data.get("papers", [])]
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        page = _robust_get(url, self.timeout)
        year_m = _YEAR.search(self.base_url) or _YEAR.search(url)
        year = year_m.group(1) if year_m else "0000"
        papers: list[Paper] = []
        seen: set[str] = set()
        for block in _IJ_ITEM.findall(page):
            title_m = _IJ_TITLE.search(block)
            if not title_m:
                continue
            title = _clean(title_m.group(1))
            if not title:
                continue
            pid_m = _IJ_PID.search(block)
            pid = pid_m.group(1) if pid_m else str(len(papers) + 1)
            paper_id = f"ijcai-{year}-{pid}"
            if paper_id in seen:
                continue
            seen.add(paper_id)
            authors = [_clean(a) for a in _IJ_AUTHOR.findall(block)]
            abstract = ""
            abs_m = _IJ_ABSTRACT.search(block)
            if abs_m:
                abstract = _clean(abs_m.group(1))
            keywords = [html.unescape(k) for k in _IJ_KW.findall(block)]
            if keywords:
                kw_text = "Keywords: " + "; ".join(keywords)
                abstract = f"{abstract}\n\n{kw_text}".strip() if abstract else kw_text
            papers.append(
                Paper(
                    paper_id=paper_id,
                    title=title,
                    url=url,          # no per-paper page; link to the listing
                    pdf_url="",       # PDFs not published for accepted papers
                    authors=authors,
                    abstract=abstract,
                )
            )
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"url": url, "papers": [p.to_dict() for p in papers]}, fh)
        except OSError:
            pass
        return papers

    def enrich_abstracts(
        self,
        papers: list[Paper],
        progress: Optional[Callable[[int, int], None]] = None,
        force_refresh: bool = False,
    ) -> list[Paper]:
        # Abstracts and authors are already present from the listing page.
        if progress:
            progress(len(papers), len(papers))
        return papers


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SOURCES = {
    "aclanthology": {
        "label": "ACL Anthology",
        "base": "https://aclanthology.org",
        "target": "acl-2026",
        "base_label": "Anthology base URL",
        "target_label": "Event (slug or full URL)",
    },
    "ijcai": {
        "label": "IJCAI",
        "base": "https://2026.ijcai.org",
        "target": "accepted-papers",
        "base_label": "Conference base URL",
        "target_label": "Accepted-papers path or full URL",
    },
}


def make_source(source: str, base_url: str, cache_dir: Optional[str] = None):
    """Return a source adapter for ``source`` (raises on unknown keys)."""
    if source == "aclanthology":
        return AnthologyScraper(base_url=base_url, cache_dir=cache_dir)
    if source == "ijcai":
        return IJCAISource(base_url=base_url, cache_dir=cache_dir)
    raise ValueError(f"Unknown source: {source}")
