"""Map a Hardcover book payload onto a bookkeep ``Book`` row.

The payload shape is what ``app.routers.hardcover.lookup_book_by_slug`` and
``lookup_book_by_title_author`` return (raw GraphQL ``books`` objects). This
mirrors the field mapping done inline in ``app.tasks.sync_missing_metadata`` so
new code has one place to call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import structlog

logger = structlog.get_logger()


def _cover_url(hc: dict) -> Optional[str]:
    img = hc.get("cached_image")
    if isinstance(img, dict):
        return img.get("url")
    return None


def _series_fields(hc: dict) -> dict[str, Any]:
    book_series = hc.get("book_series") or []
    if not book_series:
        return {}
    first = book_series[0] or {}
    series = first.get("series") or {}
    return {
        "series": series.get("name"),
        "series_id": series.get("id"),
        "series_position": first.get("position"),
    }


def _genres(hc: dict) -> Optional[str]:
    taggings = hc.get("taggings") or []
    names = [
        (t.get("tag") or {}).get("tag")
        for t in taggings
        if (t.get("tag") or {}).get("tag")
    ]
    joined = ", ".join(n for n in names if n)
    return joined or None


async def enrich_book_from_hardcover(db, book, *, overwrite: bool = True) -> bool:
    """Refresh ``book`` with the fullest metadata Hardcover has. Returns True on success.

    Uses the same data path as ``GET /api/hardcover/details`` (description, cover,
    series, genres, edition ids, ISBN). When the book has neither a Hardcover id
    nor slug, a title/author search resolves one first. ``overwrite`` is kept for
    signature compatibility; the underlying refresh always takes Hardcover's data.
    """
    from app.routers.hardcover import lookup_book_by_title_author, refresh_local_book

    hardcover_id = getattr(book, "hardcover_id", None)
    slug = getattr(book, "hardcover_slug", None)

    if not hardcover_id and not slug:
        found = await lookup_book_by_title_author(book.title, book.author, db)
        if not found:
            return False
        hardcover_id = found.get("id")
        slug = found.get("slug")
        if not hardcover_id:
            return False
        # Adopt the resolved identity on this row so refresh_local_book updates
        # it rather than creating a duplicate.
        from app.models import Book

        clash = (
            db.query(Book)
            .filter(Book.hardcover_id == hardcover_id, Book.id != book.id)
            .first()
        )
        if clash is not None:
            return False
        book.hardcover_id = hardcover_id
        book.hardcover_slug = slug or book.hardcover_slug
        db.flush()

    refreshed = await refresh_local_book(db, hardcover_id=hardcover_id, slug=slug)
    return refreshed is not None


def apply_hardcover_metadata(book, hc: dict, *, overwrite: bool = False) -> bool:
    """Copy Hardcover fields onto ``book``. Returns True if anything changed.

    With ``overwrite=False`` (default) only empty fields on ``book`` are filled,
    so hand edits and Calibre-sourced values win. ``overwrite=True`` replaces
    every field the payload provides.
    """
    if not hc:
        return False

    changed = False

    def _set(attr: str, value: Any) -> None:
        nonlocal changed
        if value in (None, "", []):
            return
        if not overwrite and getattr(book, attr, None) not in (None, "", []):
            return
        if getattr(book, attr, None) != value:
            setattr(book, attr, value)
            changed = True

    _set("description", hc.get("description"))
    _set("cover_url", _cover_url(hc))
    _set("page_count", hc.get("pages"))
    _set("rating", hc.get("rating"))
    _set("ratings_count", hc.get("ratings_count"))
    _set("users_count", hc.get("users_count"))
    _set("release_year", hc.get("release_year"))

    published = hc.get("release_date") or (
        str(hc["release_year"]) if hc.get("release_year") else None
    )
    _set("published_date", published)

    for attr, value in _series_fields(hc).items():
        _set(attr, value)

    # Genres: treated as refreshable — Hardcover's tag list is authoritative
    # when present, matching sync_missing_metadata's behavior.
    genres = _genres(hc)
    if genres and book.genres != genres:
        book.genres = genres
        changed = True

    if changed:
        book.last_refreshed = datetime.now(timezone.utc)

    return changed
