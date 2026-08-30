"""Google Books metadata client.

Google Books has the best free book *descriptions* (publisher marketing copy,
usually complete and well written) and a catalog on par with Open Library. It
also carries per-edition page counts, categories, and Google Play user ratings.
It has no concept of a book series.

Free to use without an API key at low volume (a shared ~1000 requests/day/IP
soft limit); set ``GOOGLE_BOOKS_API_KEY`` to lift that. bookkeep only calls it
for "thorough" enrichment (after a download, and the admin refresh/link
actions), never in the every-minute or daily bulk sweeps.
"""
from __future__ import annotations

import html
import os
import re
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()

_API_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = httpx.Timeout(15.0)
_MAX_GENRES = 8

# Largest to smallest image keys Google returns.
_IMAGE_KEYS = ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail")


def get_api_key() -> str:
    return os.getenv("GOOGLE_BOOKS_API_KEY", "").strip()


def _tokens(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split() if len(w) > 1}


def _title_match(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(smaller) >= 2 and smaller <= larger:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.6


def _clean_html(raw: Optional[str]) -> Optional[str]:
    """Google Books descriptions are HTML — flatten to readable plain text."""
    if not raw:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", raw)
    text = re.sub(r"(?i)</\s*p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _cover_url(image_links: dict) -> Optional[str]:
    for key in _IMAGE_KEYS:
        url = image_links.get(key)
        if url:
            url = url.replace("http://", "https://").replace("&edge=curl", "")
            return url
    return None


def _categories(cats: list[str]) -> list[str]:
    """Split Google's "Fiction / Fantasy / Epic" style categories into a short list."""
    out: list[str] = []
    seen: set[str] = set()
    for cat in cats or []:
        for part in re.split(r"\s*/\s*", cat or ""):
            p = part.strip()
            low = p.lower()
            if not p or low in seen or low in ("fiction", "nonfiction", "non-fiction", "general"):
                continue
            seen.add(low)
            out.append(p)
            if len(out) >= _MAX_GENRES:
                return out
    return out


async def _get(client: httpx.AsyncClient, params: dict) -> Optional[dict]:
    try:
        resp = await client.get(_API_URL, params=params)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("googlebooks_request_failed", error=str(exc))
        return None


async def fetch(
    *,
    isbn: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[dict]:
    """Return normalized Google Books metadata for a book, or ``None``.

    Resolves by ISBN first (exact), then by a title/author query whose top hit
    must plausibly match ``title``.
    """
    if not isbn and not title:
        return None

    params: dict[str, Any] = {"maxResults": 5}
    key = get_api_key()
    if key:
        params["key"] = key

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        info: Optional[dict] = None

        if isbn:
            cleaned = re.sub(r"[^0-9Xx]", "", isbn)
            if cleaned:
                data = await _get(client, {**params, "q": f"isbn:{cleaned}", "maxResults": 1})
                items = (data or {}).get("items") or []
                if items:
                    info = items[0].get("volumeInfo") or {}

        if info is None and title:
            q = f'intitle:"{title}"'
            if author:
                q += f' inauthor:"{author}"'
            data = await _get(client, {**params, "q": q})
            for item in (data or {}).get("items") or []:
                vi = item.get("volumeInfo") or {}
                if _title_match(title, vi.get("title") or ""):
                    info = vi
                    break

    if not info:
        return None

    rating = info.get("averageRating")
    rating = round(float(rating), 2) if isinstance(rating, (int, float)) else None
    pages = info.get("pageCount")

    return {
        "source": "googlebooks",
        "title": info.get("title"),
        "description": _clean_html(info.get("description")),
        "cover_url": _cover_url(info.get("imageLinks") or {}),
        "page_count": pages if isinstance(pages, int) and pages > 0 else None,
        "published_date": info.get("publishedDate"),
        "rating": rating,
        "ratings_count": info.get("ratingsCount"),
        "genres": _categories(info.get("categories") or []),
    }
