"""Merge book metadata from several sources onto a ``Book`` row.

No single free source is best at everything, so each field is taken from the
source that does it well, in priority order:

    description      Google Books -> Hardcover -> Open Library
    cover_url        Hardcover -> Google Books -> Open Library
    page_count       Hardcover -> Google Books -> Open Library
    published_date   Hardcover -> Google Books -> Open Library
    rating / count   Hardcover -> Google Books          (taken as a pair)
    genres           Hardcover -> Google Books -> Open Library
    series / *_id / *_position   Hardcover only

Which sources are queried depends on the call:

* ``use_google=False`` (the every-minute and daily Calibre sweeps): Open Library
  (free, unlimited) plus Hardcover only for already-linked books. Keeps
  Hardcover's rate limit and Google Books' daily quota untouched.
* ``use_google=True`` (after a download, and the admin refresh / link actions):
  all three, so the downloaded book gets Google Books' description.

``overwrite=False`` fills only empty fields; ``overwrite=True`` replaces a
field with the highest-priority source's value.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.services import googlebooks_metadata as gb
from app.services import openlibrary_metadata as ol
from app.services.hardcover_metadata import normalize as hc_normalize

logger = structlog.get_logger()

# Per-field source priority. Keys are the source dict keys used below.
_FIELD_PRIORITY = {
    "description": ("gb", "hc", "ol"),
    "cover_url": ("hc", "gb", "ol"),
    "page_count": ("hc", "gb", "ol"),
    "published_date": ("hc", "gb", "ol"),
    "series": ("hc",),
    "series_id": ("hc",),
    "series_position": ("hc",),
}
_GENRE_PRIORITY = ("hc", "gb", "ol")
_RATING_PRIORITY = ("hc", "gb")


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


async def _hardcover_payload(db, book, *, resolve: bool) -> Optional[dict]:
    """Raw Hardcover ``books`` payload for ``book`` (the shape hc_normalize wants)."""
    from app.routers.hardcover import (
        lookup_book_by_id,
        lookup_book_by_slug,
        lookup_book_by_title_author,
    )

    if getattr(book, "hardcover_slug", None):
        return await lookup_book_by_slug(book.hardcover_slug, db)
    if getattr(book, "hardcover_id", None):
        return await lookup_book_by_id(book.hardcover_id, db)
    if not resolve:
        return None

    found = await lookup_book_by_title_author(book.title, book.author, db)
    if not found or not found.get("id"):
        return None
    if not _title_match(book.title, found.get("title") or ""):
        logger.info(
            "book_metadata_hardcover_title_mismatch",
            book_id=getattr(book, "id", None),
            wanted=book.title,
            got=found.get("title"),
        )
        return None
    return found


def _merge(book, sources: dict[str, dict], *, overwrite: bool) -> bool:
    """Apply ``sources`` (``{"gb"|"ol"|"hc": <source dict>}``) onto ``book`` by priority."""
    changed = False

    def _set(attr: str, value) -> bool:
        if value in (None, "", []):
            return False
        cur = getattr(book, attr, None)
        if not overwrite and cur not in (None, "", []):
            return False
        if cur == value:
            return False
        setattr(book, attr, value)
        return True

    def _pick(field: str, order):
        for key in order:
            src = sources.get(key) or {}
            val = src.get(field)
            if val not in (None, "", []):
                return key, val
        return None, None

    for field, order in _FIELD_PRIORITY.items():
        _, value = _pick(field, order)
        changed |= _set(field, value)

    # Rating + count travel together from whichever source has the rating.
    for key in _RATING_PRIORITY:
        src = sources.get(key) or {}
        if src.get("rating") is not None:
            changed |= _set("rating", src.get("rating"))
            changed |= _set("ratings_count", src.get("ratings_count"))
            break

    # Genres: first source in priority order that has any wins the whole list.
    for key in _GENRE_PRIORITY:
        src = sources.get(key) or {}
        names = [g for g in (src.get("genres") or []) if g]
        if names:
            joined = ", ".join(dict.fromkeys(names))
            if joined and (overwrite or not book.genres) and book.genres != joined:
                book.genres = joined
                changed = True
            break

    if changed:
        book.last_refreshed = datetime.now(timezone.utc)
    return changed


async def enrich_book(
    db,
    book,
    *,
    overwrite: bool = False,
    resolve_hardcover: bool = False,
    use_google: bool = False,
) -> bool:
    """Enrich ``book`` from the configured sources. Returns True if anything changed."""
    from app.models import Book

    isbn = getattr(book, "isbn", None)
    sources: dict[str, dict] = {}

    if use_google:
        try:
            sources["gb"] = await gb.fetch(isbn=isbn, title=book.title, author=book.author) or {}
        except Exception as exc:  # best effort
            logger.warning(
                "book_metadata_googlebooks_failed",
                book_id=getattr(book, "id", None),
                error=str(exc),
            )

    def _have(field: str) -> bool:
        return any((sources.get(k) or {}).get(field) for k in ("gb", "ol"))

    # Open Library is the free fallback: skip it only when Google Books already
    # covered the fields it would contribute.
    if not (_have("description") and _have("cover_url") and _have("genres")):
        try:
            sources["ol"] = (
                await ol.fetch(isbn=isbn, title=book.title, author=book.author) or {}
            )
        except Exception as exc:
            logger.warning(
                "book_metadata_openlibrary_failed",
                book_id=getattr(book, "id", None),
                error=str(exc),
            )

    linked = getattr(book, "hardcover_id", None) or getattr(book, "hardcover_slug", None)
    want_hardcover = (
        bool(linked)
        or resolve_hardcover
        or not (sources.get("gb") or sources.get("ol"))
    )
    if want_hardcover:
        try:
            payload = await _hardcover_payload(db, book, resolve=resolve_hardcover)
        except Exception as exc:
            logger.warning("book_metadata_hardcover_fetch_failed", book_id=getattr(book, "id", None), error=str(exc))
            payload = None
        if payload:
            sources["hc"] = hc_normalize(payload)

    changed = False
    hc_src = sources.get("hc") or {}
    hc_id = hc_src.get("hardcover_id")
    if hc_id and not getattr(book, "hardcover_id", None):
        clash = db.query(Book).filter(Book.hardcover_id == hc_id).first()
        if clash is None or clash.id == getattr(book, "id", None):
            book.hardcover_id = hc_id
            book.hardcover_slug = hc_src.get("hardcover_slug") or book.hardcover_slug
            changed = True

    changed = _merge(book, sources, overwrite=overwrite) or changed
    return changed
