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


def normalize(hc: Optional[dict]) -> dict:
    """Return a source dict (see book_metadata) from a raw Hardcover ``books`` payload."""
    if not hc:
        return {}

    published = hc.get("release_date") or (
        str(hc["release_year"]) if hc.get("release_year") else None
    )
    return {
        "source": "hardcover",
        "title": hc.get("title"),
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
