"""Build a BibTeX bibliography from analysis results."""

from __future__ import annotations

import re

from .models import AnalysisResult, Paper

_YEAR = re.compile(r"(19|20)\d{2}")


def _cite_key(paper: Paper) -> str:
    key = re.sub(r"[^0-9A-Za-z]", "", paper.paper_id)
    if not key:
        key = "paper"
    if not key[0].isalpha():
        key = "p" + key
    return key


def _year(paper: Paper) -> str:
    m = _YEAR.search(paper.paper_id)
    return m.group(0) if m else ""


def _clean(value: str) -> str:
    # Strip characters that would break a BibTeX field; keep it simple and safe.
    return value.replace("{", "").replace("}", "").replace("\\", "").strip()


def build_bibtex(result: AnalysisResult) -> str:
    """Return a BibTeX string with one ``@inproceedings`` entry per relevant paper."""
    entries: list[str] = []
    seen: set[str] = set()
    for paper in result.relevant_papers:
        key = base = _cite_key(paper)
        suffix = ord("a")
        while key in seen:  # ensure unique keys
            key = f"{base}{chr(suffix)}"
            suffix += 1
        seen.add(key)

        fields = [f"  title = {{{{{_clean(paper.title)}}}}}"]
        if paper.authors:
            authors = " and ".join(_clean(a) for a in paper.authors)
            fields.append(f"  author = {{{authors}}}")
        year = _year(paper)
        if year:
            fields.append(f"  year = {{{year}}}")
        if paper.url:
            fields.append(f"  url = {{{paper.url}}}")

        entries.append("@inproceedings{" + key + ",\n" + ",\n".join(fields) + "\n}")

    header = f"% {len(entries)} papers matching \"{_clean(result.theme)}\"\n"
    return header + "\n\n".join(entries) + ("\n" if entries else "")
