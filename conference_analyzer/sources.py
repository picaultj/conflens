"""Pluggable paper sources.

Each source exposes the same small interface so the pipeline is site-agnostic:

* ``resolve_url(target) -> str``        — turn a slug/path/URL into a full URL
* ``list_papers(target, force_refresh)``— return papers (titles, links, …)
* ``enrich_abstracts(papers, …)``       — fill abstracts/authors if not already present

Sources shipping today:

* **aclanthology** — the ACL Anthology (:class:`~.scraper.AnthologyScraper`); a
  listing page plus per-paper abstract pages.
* **emnlp** — EMNLP proceedings, which also live on the ACL Anthology; the same
  adapter as ``aclanthology`` with an EMNLP event prefilled (any Anthology event
  slug works for either).
* **ijcai** — IJCAI accepted-paper pages (e.g. ``2026.ijcai.org/accepted-papers``),
  where every paper's title, authors, abstract and keywords live on one page.
* **openreview** — OpenReview venues (ICLR, NeurIPS, …) via the public JSON API;
  accepted papers are fetched by venue id (e.g. ``ICLR.cc/2024/Conference``),
  abstracts/authors/keywords/PDFs all inline.

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
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .models import Paper
from .scraper import AnthologyScraper, _clean

_UA = "conflens/0.1 (+https://github.com/picaultj/conflens)"
# Some JSON APIs (e.g. OpenReview) reject non-browser agents, so requests that
# need it can pass a browser-like User-Agent via ``headers``.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
# HTTP status codes worth retrying (transient); other 4xx fail fast.
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _robust_get(url: str, timeout: int = 120, headers: Optional[dict] = None) -> str:
    """Fetch a URL, retrying for a complete read before accepting a partial.

    ``headers`` are merged over a default ``User-Agent``. Non-transient HTTP
    errors (most 4xx) fail fast rather than burning the retry budget.
    """
    req_headers = {"User-Agent": _UA}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
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
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in _RETRY_STATUS:
                raise RuntimeError(f"Failed to fetch {url}: HTTP {e.code} {e.reason}") from e
            time.sleep(1.5 * (attempt + 1))
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
        papers = self.parse_papers(page, url)
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"url": url, "papers": [p.to_dict() for p in papers]}, fh)
        except OSError:
            pass
        return papers

    def parse_papers(self, page: str, url: str) -> list[Paper]:
        """Parse an IJCAI accepted-papers page into papers (no network)."""
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
# OpenReview source (ICLR / NeurIPS / …) via the public JSON API
# ---------------------------------------------------------------------------
def _cv(content: dict, key: str, default=None):
    """Read a content field across API v1 (bare value) and v2 ({"value": …})."""
    v = content.get(key)
    if isinstance(v, dict) and "value" in v:
        v = v["value"]
    return default if v is None else v


class OpenReviewSource:
    """Fetch accepted papers for an OpenReview venue via its public JSON API.

    Accepted papers are identified by ``content.venueid`` equal to the venue id
    (e.g. ``ICLR.cc/2024/Conference``); submissions that were rejected or
    withdrawn carry a different venueid and are therefore excluded. Works with
    both API v2 (``api2.openreview.net``, recent venues) and API v1
    (``api.openreview.net``), falling back automatically.
    """

    name = "openreview"
    _WEB = "https://openreview.net"
    _PAGE = 1000

    def __init__(
        self,
        base_url: str = "https://api2.openreview.net",
        cache_dir: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or "https://api2.openreview.net").rstrip("/")
        self.timeout = timeout
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "conference_analyzer"
        )
        os.makedirs(self.cache_dir, exist_ok=True)
        self._tok: Optional[str] = None  # None = not yet resolved; "" = anonymous

    # -- authentication (optional) -----------------------------------------
    # Anonymous access to recent (API v2) venues is challenged from some IPs;
    # a bearer token from OPENREVIEW_TOKEN, or a login with
    # OPENREVIEW_USERNAME/OPENREVIEW_PASSWORD, bypasses that.
    def _token(self) -> Optional[str]:
        if self._tok is not None:
            return self._tok or None
        direct = os.environ.get("OPENREVIEW_TOKEN")
        if direct:
            self._tok = direct.strip()
            return self._tok
        user = os.environ.get("OPENREVIEW_USERNAME") or os.environ.get("OPENREVIEW_EMAIL")
        pw = os.environ.get("OPENREVIEW_PASSWORD")
        self._tok = (self._login(user, pw) or "") if (user and pw) else ""
        return self._tok or None

    def _login(self, user: str, pw: str) -> Optional[str]:
        body = json.dumps({"id": user, "password": pw}).encode("utf-8")
        for root in self._api_roots():
            req = urllib.request.Request(
                f"{root}/login",
                data=body,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                tok = data.get("token") or data.get("access_token")
                if tok:
                    return tok
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                continue
        return None

    def _headers(self) -> dict:
        h = {"User-Agent": _BROWSER_UA, "Accept": "application/json"}
        tok = self._token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        return h

    @staticmethod
    def _venue_id(target: str) -> str:
        """Extract a venue id from a slug, a group URL, or an ``id=`` query."""
        t = (target or "").strip()
        m = re.search(r"[?&]id=([^&]+)", t)
        if m:
            return urllib.parse.unquote(m.group(1))
        return t.strip("/")

    def resolve_url(self, target: str) -> str:
        vid = self._venue_id(target) or "ICLR.cc/2024/Conference"
        return f"{self._WEB}/group?id={urllib.parse.quote(vid, safe='/.')}"

    def _api_roots(self) -> list[str]:
        roots = [self.base_url]
        if "api2.openreview.net" in self.base_url:
            roots.append(self.base_url.replace("api2.openreview.net", "api.openreview.net"))
        elif self.base_url == "https://api.openreview.net":
            roots.insert(0, "https://api2.openreview.net")
        return roots

    def _cache_path(self, vid: str) -> str:
        digest = hashlib.sha1(f"{self.base_url}|{vid}".encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"openreview_{digest}.json")

    def _fetch_notes(self, root: str, vid: str) -> list[dict]:
        """Page through every accepted note for the venue on one API root."""
        notes: list[dict] = []
        offset = 0
        while True:
            q = urllib.parse.urlencode(
                {"content.venueid": vid, "limit": self._PAGE, "offset": offset}
            )
            raw = _robust_get(f"{root}/notes?{q}", self.timeout, headers=self._headers())
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                break
            batch = data.get("notes", []) or []
            notes.extend(batch)
            if len(batch) < self._PAGE:
                break
            offset += self._PAGE
            if offset > 100000:  # safety valve against a runaway loop
                break
        return notes

    def list_papers(self, target: str, force_refresh: bool = False) -> list[Paper]:
        vid = self._venue_id(target)
        cache = self._cache_path(vid)
        if not force_refresh and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return [Paper(**p) for p in data.get("papers", [])]
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        notes: list[dict] = []
        last_err: Optional[Exception] = None
        for root in self._api_roots():
            try:
                notes = self._fetch_notes(root, vid)
            except Exception as e:  # try the next API root before giving up
                last_err = e
                continue
            if notes:
                break
        if not notes and last_err is not None:
            raise RuntimeError(
                f"OpenReview request failed ({last_err}). Recent ICLR/NeurIPS venues "
                "require authentication — set OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD "
                "(or OPENREVIEW_TOKEN) in the environment and retry."
            )
        papers = self.parse_notes(notes)
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"venue": vid, "papers": [p.to_dict() for p in papers]}, fh)
        except OSError:
            pass
        return papers

    def parse_notes(self, notes: list[dict]) -> list[Paper]:
        """Turn a list of OpenReview note dicts into papers (no network)."""
        papers: list[Paper] = []
        seen: set[str] = set()
        for note in notes:
            content = note.get("content", {}) or {}
            title = _clean(str(_cv(content, "title", "") or ""))
            if not title:
                continue
            forum = note.get("forum") or note.get("id")
            if not forum:
                continue
            paper_id = f"openreview-{note.get('id', forum)}"
            if paper_id in seen:
                continue
            seen.add(paper_id)

            authors = _cv(content, "authors", []) or []
            if isinstance(authors, str):
                authors = [authors]
            authors = [_clean(a) for a in authors if a]

            abstract = _clean(str(_cv(content, "abstract", "") or ""))
            keywords = _cv(content, "keywords", []) or []
            if isinstance(keywords, str):
                keywords = [keywords]
            if keywords:
                kw_text = "Keywords: " + "; ".join(html.unescape(str(k)) for k in keywords)
                abstract = f"{abstract}\n\n{kw_text}".strip() if abstract else kw_text

            pdf = _cv(content, "pdf")
            if isinstance(pdf, str) and pdf:
                pdf_url = pdf if pdf.startswith("http") else f"{self._WEB}{pdf}"
            else:
                pdf_url = f"{self._WEB}/pdf?id={forum}"

            papers.append(
                Paper(
                    paper_id=paper_id,
                    title=title,
                    url=f"{self._WEB}/forum?id={forum}",
                    pdf_url=pdf_url,
                    authors=authors,
                    abstract=abstract,
                )
            )
        return papers

    def enrich_abstracts(
        self,
        papers: list[Paper],
        progress: Optional[Callable[[int, int], None]] = None,
        force_refresh: bool = False,
    ) -> list[Paper]:
        # Abstracts, authors and keywords all arrive with the listing.
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
    "emnlp": {
        "label": "EMNLP (ACL Anthology)",
        "base": "https://aclanthology.org",
        "target": "emnlp-2024",
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
    "openreview": {
        "label": "OpenReview (ICLR / NeurIPS)",
        "base": "https://api2.openreview.net",
        "target": "ICLR.cc/2024/Conference",
        "base_label": "OpenReview API base",
        "target_label": "Venue ID (e.g. ICLR.cc/2024/Conference, NeurIPS.cc/2024/Conference)",
    },
}


def make_source(source: str, base_url: str, cache_dir: Optional[str] = None):
    """Return a source adapter for ``source`` (raises on unknown keys)."""
    if source in ("aclanthology", "emnlp"):
        # EMNLP proceedings live on the ACL Anthology; same adapter, different
        # default event. Any Anthology event slug works for either key.
        return AnthologyScraper(base_url=base_url, cache_dir=cache_dir)
    if source == "ijcai":
        return IJCAISource(base_url=base_url, cache_dir=cache_dir)
    if source == "openreview":
        return OpenReviewSource(base_url=base_url, cache_dir=cache_dir)
    raise ValueError(f"Unknown source: {source}")
