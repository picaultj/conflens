"""Scraping logic for the ACL Anthology (and structurally compatible sites).

The Anthology is a static Hugo site with minified, mostly-unquoted HTML, so we
parse with targeted regexes rather than a full DOM parser to keep dependencies
light and behaviour predictable.

Two levels of pages matter:

* an *event* page (e.g. ``/events/acl-2024/``) lists every paper with its
  title, landing-page URL and PDF link, but **not** its abstract; and
* a *paper* page (e.g. ``/2024.acl-long.1/``) carries the abstract plus clean
  author metadata in ``<meta name="citation_author">`` tags.

Abstracts are fetched lazily and cached on disk so re-runs are cheap.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .models import Paper

_UA = "conference-analyzer/0.1 (+https://github.com/picaultj/conference_analyzer)"
_DEFAULT_BASE = "https://aclanthology.org"

# A paper landing-page anchor, e.g.  <a ... href=/2024.acl-long.1/>Title</a>
_PAPER_ANCHOR = re.compile(
    r'<a[^>]*\bhref=(?P<q>["\']?)(?P<path>/(?P<id>\d{4}\.[a-z0-9]+-[a-z0-9]+\.\d+)/)(?P=q)[^>]*>'
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)

_ABSTRACT = re.compile(
    r'<div[^>]*class="[^"]*acl-abstract[^"]*"[^>]*>\s*'
    r"(?:<h[0-9][^>]*>.*?</h[0-9]>)?\s*<span>(?P<abs>.*?)</span>",
    re.IGNORECASE | re.DOTALL,
)

# Author meta tags appear as `<meta content="Name" name=citation_author>` — note
# the content-before-name order and unquoted attribute names in the minified HTML.
_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_CONTENT = re.compile(r'\bcontent=["\'](?P<v>[^"\']*)["\']', re.IGNORECASE)
_META_NAME = re.compile(r'\bname=["\']?citation_author["\']?', re.IGNORECASE)


def _extract_authors(page: str) -> list[str]:
    authors: list[str] = []
    for tag in _META_TAG.findall(page):
        if _META_NAME.search(tag):
            m = _META_CONTENT.search(tag)
            if m:
                authors.append(html.unescape(m.group("v")))
    return authors

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Strip tags, unescape entities and collapse whitespace."""
    return _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()


class AnthologyScraper:
    """Fetches and parses paper listings + abstracts."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        cache_dir: Optional[str] = None,
        timeout: int = 60,
        max_workers: int = 8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_workers = max_workers
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "conference_analyzer"
        )
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #
    def _get(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        last_err: Optional[Exception] = None
        partial_best = ""
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace")
            except http.client.IncompleteRead as e:
                # Large listing pages occasionally truncate. Retry for a complete
                # read so we don't cache a partial listing; keep the longest
                # partial seen as a last-resort fallback.
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

    def resolve_url(self, target: str) -> str:
        """Unified source interface — alias of :meth:`event_url`."""
        return self.event_url(target)

    def event_url(self, event: str) -> str:
        """Build an event URL from a slug, or pass through a full URL."""
        event = event.strip()
        if event.startswith("http://") or event.startswith("https://"):
            return event
        event = event.strip("/")
        if event.startswith("events/"):
            return f"{self.base_url}/{event}/"
        return f"{self.base_url}/events/{event}/"

    # ------------------------------------------------------------------ #
    # Listing
    # ------------------------------------------------------------------ #
    def _listing_cache_path(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"listing_{digest}.json")

    def list_papers(
        self,
        event: str,
        include_frontmatter: bool = False,
        force_refresh: bool = False,
    ) -> list[Paper]:
        """Return every paper listed on an event page (without abstracts).

        The parsed listing is cached on disk and keyed by event URL, so running
        several analyses (e.g. for different themes) against the same event does
        not re-download the multi-megabyte listing page. Pass
        ``force_refresh=True`` to bypass and rebuild the cache.
        """
        url = self.event_url(event)
        cache = self._listing_cache_path(url + ("|fm" if include_frontmatter else ""))
        if not force_refresh and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return [
                    Paper(
                        paper_id=p["paper_id"],
                        title=p["title"],
                        url=p["url"],
                        pdf_url=p["pdf_url"],
                    )
                    for p in data.get("papers", [])
                ]
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass  # corrupt/legacy cache — fall through and refetch

        page = self._get(url)
        seen: set[str] = set()
        papers: list[Paper] = []
        for m in _PAPER_ANCHOR.finditer(page):
            pid = m.group("id")
            if pid in seen:
                continue
            # ``*.0`` entries are the proceedings front-matter, not real papers.
            if pid.rsplit(".", 1)[-1] == "0" and not include_frontmatter:
                continue
            title = _clean(m.group("title"))
            if not title:
                continue
            seen.add(pid)
            papers.append(
                Paper(
                    paper_id=pid,
                    title=title,
                    url=f"{self.base_url}/{pid}/",
                    pdf_url=f"{self.base_url}/{pid}.pdf",
                )
            )
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "event_url": url,
                        "papers": [
                            {
                                "paper_id": p.paper_id,
                                "title": p.title,
                                "url": p.url,
                                "pdf_url": p.pdf_url,
                            }
                            for p in papers
                        ],
                    },
                    fh,
                )
        except OSError:
            pass
        return papers

    # ------------------------------------------------------------------ #
    # Abstracts / metadata
    # ------------------------------------------------------------------ #
    def _abstract_cache_path(self, pid: str) -> str:
        safe = pid.replace("/", "_")
        return os.path.join(self.cache_dir, f"{safe}.json")

    def _fetch_detail(self, paper: Paper, force_refresh: bool = False) -> Paper:
        cache = self._abstract_cache_path(paper.paper_id)
        if not force_refresh and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                paper.abstract = data.get("abstract", "")
                paper.authors = data.get("authors", [])
                return paper
            except (OSError, json.JSONDecodeError):
                pass
        try:
            page = self._get(paper.url)
        except RuntimeError:
            return paper
        m = _ABSTRACT.search(page)
        if m:
            paper.abstract = _clean(m.group("abs"))
        paper.authors = _extract_authors(page)
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"abstract": paper.abstract, "authors": paper.authors}, fh)
        except OSError:
            pass
        return paper

    def enrich_abstracts(
        self,
        papers: list[Paper],
        progress: Optional[Callable[[int, int], None]] = None,
        force_refresh: bool = False,
    ) -> list[Paper]:
        """Fetch abstracts + authors for ``papers`` concurrently (disk-cached)."""
        total = len(papers)
        done = 0
        if progress:
            progress(0, total)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {
                ex.submit(self._fetch_detail, p, force_refresh): p for p in papers
            }
            for fut in concurrent.futures.as_completed(futures):
                fut.result()
                done += 1
                if progress:
                    progress(done, total)
        return papers
