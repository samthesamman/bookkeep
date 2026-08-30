"""Apple Books metadata via the public iTunes Search API.

No API key (~20 req/min). Apple is the best free source for **high-resolution
cover art** — publishers must supply hi-res images to the store — so bookkeep
uses it at the top of the cover priority in :mod:`app.services.book_metadata`.
It also carries an English description, clean genres, and a store rating, used
only as last-resort fallbacks.

``fetch`` raises :class:`AppleBooksError` when the service is unreachable /
rate-limited (vs returning ``None`` for a plain no-match).
"""
from __future__ import annotations

import asyncio
import html
import re
from typing import Any, Optional

import httpx
import structlog

from app.services.text_match import titles_match

logger = structlog.get_logger()

_SEARCH_URL = "https://itunes.apple.com/search"
_TIMEOUT = httpx.Timeout(15.0)
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_GENRES = 8
# Apple tags every book with these — not genres.
_GENRE_NOISE = {"books", "book", "fiction", "nonfiction", "non-fiction"}


class AppleBooksError(RuntimeError):
    """Apple's iTunes Search API could not be reached / refused the request."""


def _artwork_hi_res(url100: Optional[str]) -> Optional[str]:
    """``.../100x100bb.jpg`` -> a real, native-aspect ~1400 px cover."""
    if not url100:
        return None
    out = re.sub(r"/\d+x\d+(?:[a-z]{0,3})(?:-\d+)?\.(jpg|png)(?=$|\?)", "/1400x1400bb.jpg", url100)
    return out if "1400x1400bb" in out else url100


def _clean_html(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", raw)
    text = re.sub(r"(?i)</\s*p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _genres(raw: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for g in raw or []:
        g = (g or "").strip()
        low = g.lower()
        if not g or low in seen or low in _GENRE_NOISE:
            continue
        seen.add(low)
        out.append(g)
        if len(out) >= _MAX_GENRES:
            break
    return out


async def _get(client: httpx.AsyncClient, params: dict) -> Optional[dict]:
    for attempt in range(3):
        try:
            resp = await client.get(_SEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            raise AppleBooksError(f"Apple Books unreachable: {exc}") from exc
        if resp.status_code in _RETRY_STATUSES and attempt < 2:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 429:
            raise AppleBooksError("Apple Books rate limit hit")
        if resp.status_code >= 400:
            raise AppleBooksError(f"Apple Books returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise AppleBooksError("Apple Books sent an unreadable response") from exc
    raise AppleBooksError("Apple Books did not respond after retries")


async def fetch(
    *,
    isbn: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[dict]:
    """Return normalized Apple Books metadata for a book, or ``None``.

    Resolves by ISBN first (the artwork/track often keys off it), then by a
    title/author term whose top hit must plausibly match ``title``.
    """
    if not isbn and not title:
        return None

    base = {"entity": "ebook", "country": "US", "limit": 15}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        result: Optional[dict] = None

        if isbn:
            cleaned = re.sub(r"[^0-9Xx]", "", isbn)
            if cleaned:
                data = await _get(client, {**base, "term": cleaned})
                for r in (data or {}).get("results") or []:
                    if r.get("artworkUrl100"):
                        result = r
                        break

        if result is None and title:
            term = f"{title} {author}".strip() if author else title
            data = await _get(client, {**base, "term": term})
            for r in (data or {}).get("results") or []:
                if titles_match(title, r.get("trackName") or "") and r.get("artworkUrl100"):
                    result = r
                    break

        if result is None:
            return None

    rating = result.get("averageUserRating")
    rating = round(float(rating), 2) if isinstance(rating, (int, float)) and rating else None
    pub = result.get("releaseDate")
    if isinstance(pub, str) and len(pub) >= 10:
        pub = pub[:10]

    return {
        "source": "applebooks",
        "title": result.get("trackName"),
        "author": result.get("artistName"),
        "cover_url": _artwork_hi_res(result.get("artworkUrl100")),
        "description": _clean_html(result.get("description")),
        "published_date": pub if isinstance(pub, str) else None,
        "rating": rating,
        "ratings_count": result.get("userRatingCount"),
        "genres": _genres(result.get("genres") or []),
    }
