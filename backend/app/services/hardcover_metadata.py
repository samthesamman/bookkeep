"""Normalize a Hardcover book payload into the shape :mod:`app.services.book_metadata` merges.

The payload is what ``app.routers.hardcover.lookup_book_by_slug`` /
``lookup_book_by_id`` / ``lookup_book_by_title_author`` return (raw GraphQL
``books`` objects). Hardcover is bookkeep's source of record for a book's
**series** and **community rating**; the other fields it provides are used only
as fallbacks behind Google Books / Open Library.
"""
from __future__ import annotations

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


def _genres(hc: dict) -> list[str]:
    taggings = hc.get("taggings") or []
    return [
        (t.get("tag") or {}).get("tag")
        for t in taggings
        if (t.get("tag") or {}).get("tag")
    ]


def _author(hc: dict) -> Optional[str]:
    names = []
    for c in hc.get("contributions") or []:
        name = (c.get("author") or {}).get("name")
        if name and name not in names:
            names.append(name)
    if not names and hc.get("cached_contributors"):
        for c in hc["cached_contributors"]:
            name = c.get("author", {}).get("name") if isinstance(c.get("author"), dict) else c.get("name")
            if name and name not in names:
                names.append(name)
    return ", ".join(names[:3]) or None


def _publisher_and_isbn(hc: dict) -> tuple[Optional[str], Optional[str]]:
    editions = hc.get("editions") or []
    # Prefer English editions; fall back to whatever is most-read.
    english = [e for e in editions if (e.get("language") or {}).get("code3") == "eng"]
    ordered = english + [e for e in editions if e not in english]

    publisher = next(
        ((e.get("publisher") or {}).get("name") for e in ordered if (e.get("publisher") or {}).get("name")),
        None,
    )
    isbn = next(
        (e.get("isbn_13") or e.get("isbn_10") for e in ordered if e.get("isbn_13") or e.get("isbn_10")),
        None,
    )
    return publisher, isbn


def normalize(hc: Optional[dict]) -> dict:
    """Return a source dict (see book_metadata) from a raw Hardcover ``books`` payload."""
    if not hc:
        return {}

    published = hc.get("release_date") or (
        str(hc["release_year"]) if hc.get("release_year") else None
    )
    publisher, isbn = _publisher_and_isbn(hc)
    return {
        "source": "hardcover",
        "title": hc.get("title"),
        "author": _author(hc),
        "publisher": publisher,
        "isbn": isbn,
        "description": hc.get("description"),
        "cover_url": _cover_url(hc),
        "page_count": hc.get("pages"),
        "published_date": published,
        "rating": hc.get("rating"),
        "ratings_count": hc.get("ratings_count"),
        "genres": _genres(hc),
        "hardcover_id": hc.get("id"),
        "hardcover_slug": hc.get("slug"),
        **_series_fields(hc),
    }
