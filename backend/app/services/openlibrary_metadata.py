"""Open Library metadata client.

Open Library has no API key and no meaningful rate limit (they ask only for a
descriptive ``User-Agent`` and reasonable pacing), and a broad catalog. It
carries a cover, page count, first-published year, and subjects, and sometimes
a description. It does *not* carry series information or trustworthy community
ratings. bookkeep uses it as the free fallback in every metadata pass; see
:mod:`app.services.book_metadata` for how sources are merged.

Two HTTP calls at most per book: one ``search.json`` (by ISBN, then by
title/author) and one ``works/<id>.json`` for the description and curated
subjects.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()

_SEARCH_URL = "https://openlibrary.org/search.json"
_WORKS_URL = "https://openlibrary.org/works/{key}.json"
_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

# Open Library asks automated clients to identify themselves.
_HEADERS = {"User-Agent": "bookkeep/1.0 (+https://github.com/; self-hosted library manager)"}
_TIMEOUT = httpx.Timeout(15.0)

_SEARCH_FIELDS = ",".join(
    [
        "key",
        "title",
        "author_name",
        "first_publish_year",
        "cover_i",
        "number_of_pages_median",
        "subject",
    ]
)

# Substrings of Open Library "subjects" that are shelving/annotation noise, not genres.
_SUBJECT_NOISE = (
    "accessible book",
    "protected daisy",
    "in library",
    "large type books",
    "internet archive wishlist",
    "overdrive",
    "reading level",
    "lending library",
    "nyt:",
    "new york times bestseller",
)
_MAX_GENRES = 8


def _tokens(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split() if len(w) > 1}


def _title_match(a: str, b: str) -> bool:
    """True when two titles plausibly name the same book.

    Exact token-set equality always matches; otherwise the shorter side must be
    fully contained in the longer with at least two shared tokens (a subtitle on
    one side only), or the two must have a strong overlap. Single-word titles
    match only exactly, so "Dune" never matches "Dune Messiah".
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(smaller) >= 2 and smaller <= larger:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.6


def _clean_subjects(subjects: list[str]) -> list[str]:
    """Trim Open Library's noisy subject list down to a short genre-ish list."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in subjects or []:
        s = (raw or "").strip()
        low = s.lower()
        if not s or low in seen:
            continue
        if any(noise in low for noise in _SUBJECT_NOISE):
            continue
        if len(s) > 40 or s.count(" ") > 4:
            continue
        seen.add(low)
        out.append(s if s.istitle() else s.title())
        if len(out) >= _MAX_GENRES:
            break
    return out


def _description_text(work: dict) -> Optional[str]:
    desc = work.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value")
    if not isinstance(desc, str):
        return None
    text = desc.strip()
    # Open Library descriptions often append a source note after a horizontal rule.
    for marker in ("\n----", "\r\n----", "\n----------", "([source]", "\n\n([source"):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx].strip()
    return text or None


async def _get_json(
    client: httpx.AsyncClient, url: str, params: Optional[dict] = None
) -> Optional[Any]:
    try:
        resp = await client.get(url, params=params, headers=_HEADERS, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("openlibrary_request_failed", url=url, error=str(exc))
        return None


async def fetch(
    *,
    isbn: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[dict]:
    """Return normalized Open Library metadata for a book, or ``None``.

    Resolves by ISBN first (exact), then by a title/author search whose top hit
    must plausibly match ``title``.
    """
    if not isbn and not title:
        return None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        doc: Optional[dict] = None

        if isbn:
            cleaned = re.sub(r"[^0-9Xx]", "", isbn)
            if cleaned:
                data = await _get_json(
                    client,
                    _SEARCH_URL,
                    {"isbn": cleaned, "fields": _SEARCH_FIELDS, "limit": 1},
                )
                docs = (data or {}).get("docs") or []
                if docs:
                    doc = docs[0]

        if doc is None and title:
            params = {"title": title, "fields": _SEARCH_FIELDS, "limit": 5}
            if author:
                params["author"] = author
            data = await _get_json(client, _SEARCH_URL, params)
            for cand in (data or {}).get("docs") or []:
                if _title_match(title, cand.get("title") or ""):
                    doc = cand
                    break

        if doc is None:
            return None

        work_key = (doc.get("key") or "").rsplit("/", 1)[-1]
        work: dict = {}
        if work_key:
            work = await _get_json(client, _WORKS_URL.format(key=work_key)) or {}

    genres = _clean_subjects(work.get("subjects") or doc.get("subject") or [])
    cover_url = _COVER_URL.format(cover_id=doc["cover_i"]) if doc.get("cover_i") else None
    year = doc.get("first_publish_year")

    return {
        "source": "openlibrary",
        "work_key": work_key or None,
        "title": doc.get("title"),
        "description": _description_text(work),
        "cover_url": cover_url,
        "page_count": doc.get("number_of_pages_median"),
        "published_date": str(year) if year else None,
        "genres": genres,
    }
