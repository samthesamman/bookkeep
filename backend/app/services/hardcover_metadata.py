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


async def fetch_hardcover_payload(
    db,
    *,
    slug: Optional[str] = None,
    hardcover_id: Optional[int] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
) -> Optional[dict]:
    """Resolve a Hardcover book payload by slug, id, or title/author search."""
    from app.routers.hardcover import (
        execute_graphql,
        lookup_book_by_slug,
        lookup_book_by_title_author,
    )

    if slug:
        data = await lookup_book_by_slug(slug, db)
        if data:
            return data
    if hardcover_id:
        data = await execute_graphql(
            """
            query GetBook($id: Int!) {
              books_by_pk(id: $id) {
                id title slug release_year release_date pages description
                cached_image rating ratings_count users_count
                book_series { position series { id name } }
                taggings(limit: 10) { tag { tag } }
              }
            }
            """,
            {"id": int(hardcover_id)},
            db,
        )
        book = (data or {}).get("books_by_pk")
        if book:
            return book
    if title:
        data = await lookup_book_by_title_author(title, author, db)
        if data:
            return data
    return None


async def enrich_book_from_hardcover(
    db,
    book,
    *,
    slug: Optional[str] = None,
    hardcover_id: Optional[int] = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
    overwrite: bool = False,
) -> bool:
    """Fetch Hardcover metadata for ``book`` and apply it. Returns True if changed.

    Also backfills ``book.hardcover_id`` / ``book.hardcover_slug`` when the
    lookup resolves them and the book has none.
    """
    hc = await fetch_hardcover_payload(
        db,
        slug=slug or getattr(book, "hardcover_slug", None),
        hardcover_id=hardcover_id or getattr(book, "hardcover_id", None),
        title=title or getattr(book, "title", None),
        author=author or getattr(book, "author", None),
    )
    if not hc:
        return False

    changed = False
    hc_id = hc.get("id")
    if hc_id and not book.hardcover_id:
        dupe = None
        try:
            from app.models import Book

            dupe = (
                db.query(Book)
                .filter(Book.hardcover_id == hc_id, Book.id != book.id)
                .first()
            )
        except Exception:
            dupe = None
        if dupe is None:
            book.hardcover_id = hc_id
            changed = True
    if hc.get("slug") and not getattr(book, "hardcover_slug", None):
        book.hardcover_slug = hc.get("slug")
        changed = True

    return apply_hardcover_metadata(book, hc, overwrite=overwrite) or changed


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
