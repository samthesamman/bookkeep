"""Google Books metadata client.

Google Books has the best free book *descriptions* (publisher marketing copy,
usually complete and well written) and a catalog on par with Open Library. It
also carries per-edition page counts, categories, and Google Play user ratings.
It has no concept of a book series.

Without an API key the endpoint shares a small per-IP quota and returns HTTP 429
very readily — set ``GOOGLE_BOOKS_API_KEY`` (free, ~1000 req/day) to make it
usable. bookkeep only calls Google Books for "thorough" enrichment (after a
download, and the admin refresh / source-picker actions), never in the
every-minute or daily bulk sweeps. ``fetch`` raises :class:`GoogleBooksError`
when the service is unreachable / rate-limited (vs returning ``None`` for a
plain no-match).
"""
from __future__ import annotations

import asyncio
import html
import os
import re
from typing import Any, Optional

import httpx
import structlog

from app.services.text_match import titles_match

logger = structlog.get_logger()

_API_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = httpx.Timeout(15.0)
_MAX_GENRES = 8
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class GoogleBooksError(RuntimeError):
    """Google Books could not be reached / refused the request (not just 'no match')."""

# Largest to smallest image keys Google returns.
_IMAGE_KEYS = ("extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail")


def get_api_key() -> str:
    return os.getenv("GOOGLE_BOOKS_API_KEY", "").strip()


def get_country() -> str:
    """ISO country for the ``country`` param. Without it Google strips
    ``description`` / ``imageLinks`` from volumes it treats as geo-restricted."""
    return (os.getenv("GOOGLE_BOOKS_COUNTRY", "").strip() or "US").upper()


def _richness(vi: dict) -> int:
    """How complete a volume's metadata is — used to pick the best of several hits."""
    score = 0
    if _clean_html(vi.get("description")):
        score += 2
    if _cover_url(vi.get("imageLinks") or {}):
        score += 1
    if vi.get("publisher"):
        score += 1
    if isinstance(vi.get("pageCount"), int) and vi["pageCount"] > 0:
        score += 1
    return score


def _is_english(vi: dict) -> bool:
    lang = (vi.get("language") or "").lower()
    return not lang or lang.startswith("en")


def _is_stub(vi: Optional[dict]) -> bool:
    """A catalog-only entry (Google has the ISBN but no real content): no
    description, no publisher, 0 pages. Its ``imageLinks`` is a placeholder."""
    if not vi:
        return True
    return (
        not vi.get("description")
        and not vi.get("publisher")
        and not (isinstance(vi.get("pageCount"), int) and vi["pageCount"] > 0)
    )


def _matching_items(items, wanted_title: Optional[str]) -> list[dict]:
    """Search-result items (``{id, volumeInfo}``) whose title matches ``wanted_title``
    (all of them if ``wanted_title`` is None — e.g. an exact ISBN lookup)."""
    out = []
    for item in items or []:
        vi = item.get("volumeInfo") or {}
        if wanted_title is None or titles_match(wanted_title, _volume_title(vi)):
            out.append(item)
    return out


def _has_solid_english_match(items: list[dict]) -> bool:
    """An English item that already looks real (not a bare catalog stub) — no
    point running the fallback title search if we have one of these."""
    return any(
        _is_english(it.get("volumeInfo") or {}) and not _is_stub(it.get("volumeInfo") or {})
        for it in items
    )


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
    """A *real*, upsized front-cover URL from Google's imageLinks, or ``None``.

    Google serves two kinds of ``imageLinks``:
      * publisher art (``/books/publisher/content``) or a link that comes in
        several sizes — a real cover, safe to request at ~800px via ``fife``;
      * a bare ``thumbnail`` from ``/books/content`` on a catalog-only record —
        that's the *only* image Google has, ~128px, and asking for anything
        bigger renders a grey placeholder.
    We only return the first kind; the second yields ``None`` so a good existing
    cover (Hardcover's) is kept when this source is applied.
    """
    urls = [u for u in (image_links.get(k) for k in _IMAGE_KEYS) if u]
    if not urls:
        return None

    has_variants = any(image_links.get(k) for k in ("small", "medium", "large", "extraLarge"))
    from_publisher = any("/books/publisher/content" in u for u in urls)
    if not has_variants and not from_publisher:
        return None

    best = next((u for u in urls if "printsec=frontcover" in u), None)
    if best is None:
        best = next((u for u in urls if "pg=" not in u), None)
    if best is None:
        return None

    # Strip the fixed zoom, page-curl, and the per-request imgtk token (not
    # needed for frontcover URLs, and it expires — we store this URL).
    url = re.sub(
        r"&(?:edge=curl|zoom=\d+|imgtk=[^&]*)", "", best.replace("http://", "https://")
    )
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}fife=w800"


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


async def _get(client: httpx.AsyncClient, params: dict, *, url: str = _API_URL) -> Optional[dict]:
    """One Google Books request with retry on 429/5xx. Raises GoogleBooksError on failure."""
    for attempt in range(3):
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            raise GoogleBooksError(f"Google Books unreachable: {exc}") from exc

        if resp.status_code in _RETRY_STATUSES and attempt < 2:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 429:
            raise GoogleBooksError(
                "Google Books rate limit hit — set GOOGLE_BOOKS_API_KEY to raise the quota"
            )
        if resp.status_code >= 400:
            raise GoogleBooksError(f"Google Books returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise GoogleBooksError("Google Books sent an unreadable response") from exc
    raise GoogleBooksError("Google Books did not respond after retries")


def _volume_title(vi: dict) -> str:
    t = (vi.get("title") or "").strip()
    sub = (vi.get("subtitle") or "").strip()
    return f"{t}: {sub}" if t and sub else t


async def _hydrate(client, params: dict, item: Optional[dict]) -> dict:
    """Full ``volumeInfo`` for a search-result item.

    Google's search results carry a *truncated* volumeInfo — no ``description``,
    often no ``imageLinks`` or ``pageCount`` even when the record has them. The
    single-volume ``GET /volumes/{id}`` returns the complete record.
    """
    vi = item.get("volumeInfo") or {} if item else {}
    vid = item.get("id") if item else None
    if not vid:
        return vi
    q = {k: v for k, v in params.items() if k in ("key", "country")}
    try:
        data = await _get(client, q, url=f"{_API_URL}/{vid}")
    except GoogleBooksError:
        return vi  # keep the truncated record rather than nothing
    return (data or {}).get("volumeInfo") or vi


async def fetch(
    *,
    isbn: Optional[str] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[dict]:
    """Return normalized Google Books metadata for a book, or ``None``.

    ISBN lookup first, then a plain title/author keyword query. Google returns
    many editions; the best title-matching English item and the best overall
    item are each hydrated with ``GET /volumes/{id}`` (search results are
    truncated). The English record supplies text (description, categories,
    length, date); the richest record of any language supplies language-neutral
    fields (cover, publisher, ISBN, rating).
    """
    if not isbn and not title:
        return None

    params: dict[str, Any] = {"country": get_country()}
    key = get_api_key()
    if key:
        params["key"] = key

    items: list[dict] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if isbn:
            cleaned = re.sub(r"[^0-9Xx]", "", isbn)
            if cleaned:
                data = await _get(client, {**params, "q": f"isbn:{cleaned}", "maxResults": 5})
                items += _matching_items((data or {}).get("items"), None)

        # A title search too, unless the ISBN already produced a solid English hit.
        if title and not _has_solid_english_match(items):
            main = title.split(":", 1)[0].strip()
            queries = [f"{title} {author}".strip() if author else title]
            if main and main.lower() != title.strip().lower():
                queries.append(f"{main} {author}".strip() if author else main)

            for q in queries:
                data = await _get(client, {**params, "q": q, "maxResults": 20})
                items += _matching_items((data or {}).get("items"), title)
                if _has_solid_english_match(items):
                    break

        if not items:
            return None

        def _item_richness(it):
            return _richness(it.get("volumeInfo") or {})

        english_items = [it for it in items if _is_english(it.get("volumeInfo") or {})]
        en_item = max(english_items, key=_item_richness) if english_items else None
        best_item = max(items, key=_item_richness)

        en = await _hydrate(client, params, en_item) if en_item else None
        best = en if best_item is en_item else await _hydrate(client, params, best_item)

    if en is None and best is None:
        return None

    def _pick(field):
        """Prefer the English edition, fall back to the richest of any language."""
        for src in (en, best):
            v = (src or {}).get(field)
            if v not in (None, "", [], 0):
                return v
        return None

    # Edition-specific text — English edition only, else nothing (Hardcover /
    # Open Library fill the English description/length in the merge).
    en_desc = _clean_html((en or {}).get("description"))
    en_genres = _categories((en or {}).get("categories") or [])
    en_pages = (en or {}).get("pageCount")
    en_pubdate = (en or {}).get("publishedDate")

    # Skip a stub's placeholder cover; take a real one from any edition.
    cover_url = None
    for src in ((en, best) if not _is_stub(en) else (best, en)):
        cover_url = _cover_url((src or {}).get("imageLinks") or {})
        if cover_url:
            break

    idents = _pick("industryIdentifiers") or []
    isbn_out = next(
        (i.get("identifier") for i in idents if i.get("type") == "ISBN_13"),
        next((i.get("identifier") for i in idents if i.get("type") == "ISBN_10"), None),
    )
    rating = _pick("averageRating")
    rating = round(float(rating), 2) if isinstance(rating, (int, float)) else None
    authors = [a for a in (_pick("authors") or []) if a]

    if not any([en_desc, cover_url, isbn_out, _pick("publisher"), authors, en_pages]):
        return None  # nothing worth showing

    return {
        "source": "googlebooks",
        "title": (en or best or {}).get("title"),
        "author": ", ".join(authors) or None,
        "publisher": _pick("publisher"),
        "isbn": isbn_out,
        "description": en_desc,
        "cover_url": cover_url,
        "page_count": en_pages if isinstance(en_pages, int) and en_pages > 0 else None,
        "published_date": en_pubdate,
        "rating": rating,
        "ratings_count": _pick("ratingsCount"),
        "genres": en_genres,
    }
