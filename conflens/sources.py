"""Pluggable paper sources.

Each source exposes the same small interface so the pipeline is site-agnostic:

* ``resolve_url(target) -> str``        — turn a slug/path/URL into a full URL
* ``list_papers(target, force_refresh)``— return papers (titles, links, …)
* ``enrich_abstracts(papers, …)``       — fill abstracts/authors if not already present

Sources shipping today:

* **aclanthology** — the ACL Anthology (:class:`~.scraper.AnthologyScraper`); a
  listing page plus per-paper abstract pages.
* **emnlp** / **naacl** — EMNLP and NAACL proceedings, which also live on the ACL
  Anthology; the same adapter as ``aclanthology`` with the respective event
  prefilled (any Anthology event slug works for any of these keys).
* **ijcai** — IJCAI accepted-paper pages (e.g. ``2026.ijcai.org/accepted-papers``),
  where every paper's title, authors, abstract and keywords live on one page.
* **openreview** — OpenReview venues (ICLR, NeurIPS, …) via the public JSON API;
  accepted papers are fetched by venue id (e.g. ``ICLR.cc/2024/Conference``),
  abstracts/authors/keywords/PDFs all inline.
* **pscc** — Power Systems Computation Conference; a per-year HTML fragment from
  the papers-repository endpoint gives titles, authors and PDF links (abstracts
  aren't published on the site, so classification is title-based).
* **isgteurope** — IEEE PES ISGT Europe, via the public DBLP search API (many
  IEEE-Xplore-only venues are indexed there). Titles, authors and a DOI link per
  paper; no abstracts, so classification is title-based. The generic
  :class:`DBLPSource` behind it works for any DBLP-indexed conference.

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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            # ConnectionError covers a peer reset mid-response (e.g. rate-limiting).
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
            os.path.expanduser("~"), ".cache", "conflens"
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
            os.path.expanduser("~"), ".cache", "conflens"
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
# PSCC source (Power Systems Computation Conference)
# ---------------------------------------------------------------------------
# The PSCC papers repository is served by a small PHP endpoint that returns an
# HTML fragment of <div class='paper'> blocks for a given year. Titles, authors
# and a PDF link are inline; abstracts live only inside the PDFs, so papers are
# classified on their titles (the classifier already handles an empty abstract).
_PSCC_BLOCK = "<div class='paper'>"
_PSCC_TITLE = re.compile(r"<p class='title'>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_PSCC_AUTHORS = re.compile(r"<p class='authors'>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_PSCC_PDF = re.compile(r"href='(repo/papers/[^']+\.pdf)'", re.IGNORECASE)
_PSCC_PID = re.compile(r"([0-9]{4}_[0-9]+)\.pdf", re.IGNORECASE)


class PSCCSource:
    """Fetch a PSCC edition's papers from the papers-repository endpoint.

    ``target`` is a conference year (e.g. ``2024``) or a full listing URL. Only
    titles, authors and PDF links are available on the site; abstracts are not,
    so downstream classification is title-based.
    """

    name = "pscc"

    def __init__(
        self,
        base_url: str = "https://pscc-central.epfl.ch",
        cache_dir: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or "https://pscc-central.epfl.ch").rstrip("/")
        self.timeout = timeout
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "conflens"
        )
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _year(target: str) -> str:
        m = re.search(r"(19|20)\d{2}", target or "")
        return m.group(0) if m else (target or "").strip()

    def _api_url(self, year: str) -> str:
        return f"{self.base_url}/repo/make_table.php?authors=&year={year}&title="

    def resolve_url(self, target: str) -> str:
        t = (target or "").strip()
        if t.startswith("http://") or t.startswith("https://"):
            return t
        return f"{self.base_url}/papers-repo"

    def _cache_path(self, year: str) -> str:
        digest = hashlib.sha1(f"{self.base_url}|{year}".encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"pscc_{digest}.json")

    def list_papers(self, target: str, force_refresh: bool = False) -> list[Paper]:
        year = self._year(target)
        cache = self._cache_path(year)
        if not force_refresh and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return [Paper(**p) for p in data.get("papers", [])]
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        t = (target or "").strip()
        url = t if t.startswith(("http://", "https://")) else self._api_url(year)
        page = _robust_get(url, self.timeout, headers={"User-Agent": _BROWSER_UA})
        papers = self.parse_papers(page, year)
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"year": year, "papers": [p.to_dict() for p in papers]}, fh)
        except OSError:
            pass
        return papers

    def parse_papers(self, page: str, year: str = "") -> list[Paper]:
        """Parse a PSCC make_table.php fragment into papers (no network)."""
        papers: list[Paper] = []
        seen: set[str] = set()
        for block in page.split(_PSCC_BLOCK)[1:]:
            title_m = _PSCC_TITLE.search(block)
            if not title_m:
                continue
            title = _clean(title_m.group(1))
            if not title:
                continue
            pdf_m = _PSCC_PDF.search(block)
            pdf_rel = pdf_m.group(1) if pdf_m else ""
            pid_m = _PSCC_PID.search(pdf_rel)
            pid = pid_m.group(1) if pid_m else f"{year or '0000'}-{len(papers) + 1}"
            paper_id = f"pscc-{pid}"
            if paper_id in seen:
                continue
            seen.add(paper_id)
            authors_m = _PSCC_AUTHORS.search(block)
            authors: list[str] = []
            if authors_m:
                authors = [a for a in (_clean(x) for x in authors_m.group(1).split(",")) if a]
            pdf_url = f"{self.base_url}/{pdf_rel}" if pdf_rel else ""
            papers.append(
                Paper(
                    paper_id=paper_id,
                    title=title,
                    url=pdf_url or f"{self.base_url}/papers-repo",
                    pdf_url=pdf_url,
                    authors=authors,
                    abstract="",  # abstracts aren't published on the PSCC site
                )
            )
        return papers

    def enrich_abstracts(
        self,
        papers: list[Paper],
        progress: Optional[Callable[[int, int], None]] = None,
        force_refresh: bool = False,
    ) -> list[Paper]:
        # No abstract source on the site; titles + authors arrive with the listing.
        if progress:
            progress(len(papers), len(papers))
        return papers


# ---------------------------------------------------------------------------
# DBLP source (open metadata index — e.g. IEEE PES ISGT Europe)
# ---------------------------------------------------------------------------
# DBLP indexes many conferences that are otherwise only on IEEE Xplore. Its
# public search API returns, per proceedings, each paper's title, authors and a
# link (usually a DOI) — but no abstract, so classification is title-based.
_DBLP_DISAMBIG = re.compile(r"\s+\d{4}$")   # DBLP appends "0001"-style suffixes
_DBLP_TRAIL_DOT = re.compile(r"\.\s*$")


class DBLPSource:
    """Fetch a conference edition's papers from the public DBLP search API.

    ``target`` is a ``"venue year"`` pair (e.g. ``"isgteurope 2024"``), a DBLP
    proceedings key (``conf/isgteurope/isgteurope2024``) or a DBLP URL. Only
    titles, authors and a link (usually the DOI) are available — abstracts are
    not, so downstream classification is title-based.
    """

    name = "dblp"
    _PAGE = 100  # DBLP's public search API caps hits-per-request at 100

    def __init__(
        self,
        base_url: str = "https://dblp.org",
        cache_dir: Optional[str] = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or "https://dblp.org").rstrip("/")
        self.timeout = timeout
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "conflens"
        )
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _proc_key(target: str) -> str:
        """Normalise ``target`` to a DBLP proceedings key like ``conf/x/x2024``."""
        t = (target or "").strip()
        m = re.search(r"(conf/[^\s?#]+?)(?:\.html|\.bht)?(?:[?#].*)?$", t)
        if m:
            return m.group(1)
        parts = t.split()
        venue = parts[0].strip("/") if parts else ""
        year = ""
        for p in parts[1:]:
            ym = re.search(r"(19|20)\d{2}", p)
            if ym:
                year = ym.group(0)
                break
        return f"conf/{venue}/{venue}{year}"

    def resolve_url(self, target: str) -> str:
        return f"{self.base_url}/db/{self._proc_key(target)}.html"

    def _cache_path(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"dblp_{digest}.json")

    def list_papers(self, target: str, force_refresh: bool = False) -> list[Paper]:
        key = self._proc_key(target)
        cache = self._cache_path(key)
        if not force_refresh and os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return [Paper(**p) for p in data.get("papers", [])]
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        hits = self._fetch_hits(key)
        papers = self.parse_hits(hits)
        try:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"key": key, "papers": [p.to_dict() for p in papers]}, fh)
        except OSError:
            pass
        return papers

    def _fetch_hits(self, proc_key: str) -> list[dict]:
        """Page through every DBLP record whose TOC is this proceedings."""
        toc = f"db/{proc_key}.bht"
        hits: list[dict] = []
        first = 0
        while True:
            if first:  # be polite to DBLP between pages to avoid throttling
                time.sleep(0.7)
            q = urllib.parse.urlencode(
                {"q": f"toc:{toc}:", "format": "json", "h": self._PAGE, "f": first, "c": 0}
            )
            raw = _robust_get(
                f"{self.base_url}/search/publ/api?{q}",
                self.timeout,
                headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
            )
            try:
                result = json.loads(raw).get("result", {})
            except json.JSONDecodeError:
                break
            batch = result.get("hits", {}).get("hit", []) or []
            hits.extend(batch)
            total = int(result.get("hits", {}).get("@total", len(hits)) or 0)
            first += self._PAGE
            if first >= total or not batch:
                break
        return hits

    def parse_hits(self, hits: list[dict]) -> list[Paper]:
        """Turn DBLP search hits into papers (no network)."""
        papers: list[Paper] = []
        seen: set[str] = set()
        for hit in hits:
            info = hit.get("info", {}) or {}
            title = _DBLP_TRAIL_DOT.sub("", _clean(str(info.get("title", "") or "")))
            if not title:
                continue
            key = info.get("key") or hit.get("@id") or f"{len(papers) + 1}"
            paper_id = "dblp-" + str(key).replace("/", "-")
            if paper_id in seen:
                continue
            seen.add(paper_id)

            raw_authors = (info.get("authors", {}) or {}).get("author", [])
            if isinstance(raw_authors, dict):
                raw_authors = [raw_authors]
            authors = []
            for a in raw_authors:
                name = a.get("text", "") if isinstance(a, dict) else str(a)
                name = _DBLP_DISAMBIG.sub("", _clean(name))
                if name:
                    authors.append(name)

            ee = info.get("ee") or info.get("url") or ""
            if isinstance(ee, list):
                ee = ee[0] if ee else ""
            papers.append(
                Paper(
                    paper_id=paper_id,
                    title=title,
                    url=ee,          # DOI / landing page (full text is usually paywalled)
                    pdf_url="",      # no open PDF via DBLP
                    authors=authors,
                    abstract="",     # DBLP has no abstracts → title-based classification
                )
            )
        return papers

    def enrich_abstracts(
        self,
        papers: list[Paper],
        progress: Optional[Callable[[int, int], None]] = None,
        force_refresh: bool = False,
    ) -> list[Paper]:
        # DBLP exposes no abstracts; titles + authors arrive with the listing.
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
    "naacl": {
        "label": "NAACL (ACL Anthology)",
        "base": "https://aclanthology.org",
        "target": "naacl-2024",
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
    "pscc": {
        "label": "PSCC (Power Systems Computation Conf.)",
        "base": "https://pscc-central.epfl.ch",
        "target": "2024",
        "base_label": "PSCC base URL",
        "target_label": "Conference year (e.g. 2024, 2022) or full listing URL",
    },
    "isgteurope": {
        "label": "ISGT Europe (IEEE PES, via DBLP)",
        "base": "https://dblp.org",
        "target": "isgteurope 2024",
        "base_label": "DBLP base URL",
        "target_label": "DBLP venue + year (e.g. isgteurope 2024) or proceedings key",
    },
}


def make_source(source: str, base_url: str, cache_dir: Optional[str] = None):
    """Return a source adapter for ``source`` (raises on unknown keys)."""
    if source in ("aclanthology", "emnlp", "naacl"):
        # EMNLP and NAACL proceedings live on the ACL Anthology; same adapter,
        # different default event. Any Anthology event slug works for any key.
        return AnthologyScraper(base_url=base_url, cache_dir=cache_dir)
    if source == "ijcai":
        return IJCAISource(base_url=base_url, cache_dir=cache_dir)
    if source == "openreview":
        return OpenReviewSource(base_url=base_url, cache_dir=cache_dir)
    if source == "pscc":
        return PSCCSource(base_url=base_url, cache_dir=cache_dir)
    if source in ("isgteurope", "dblp"):
        # DBLP-backed; ISGT Europe (IEEE PES) is the registered venue, but any
        # DBLP-indexed conference works by passing "<venue> <year>" as the target.
        return DBLPSource(base_url=base_url, cache_dir=cache_dir)
    raise ValueError(f"Unknown source: {source}")
