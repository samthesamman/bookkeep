"""New York Times Books API client (Best Sellers lists).

The NYT Books API is the authoritative source for the "NYT Best Sellers" lists.
It is free but rate limited (a few requests/minute, ~500-1000/day) and its terms
require visible attribution.  We only ever make one ``full-overview`` call per day
(cached), plus a ``names`` call when an admin opens the Settings picker.

The API key is read from the ``NYT_BOOKS_API_KEY`` environment variable only.
"""
import asyncio
import os
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()

NYT_API_BASE = "https://api.nytimes.com/svc/books/v3"

# Shown on the Discover page and returned with the payload (NYT terms of use).
NYT_ATTRIBUTION = "Data provided by The New York Times"

# Used when an admin has not picked any lists yet.
DEFAULT_LIST_SLUGS = [
    "combined-print-and-e-book-fiction",
    "combined-print-and-e-book-nonfiction",
]


def get_nyt_api_key() -> str:
    """Return the NYT Books API key from the environment (empty string if unset)."""
    return os.getenv("NYT_BOOKS_API_KEY", "").strip()


async def _get_json(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """GET ``{NYT_API_BASE}{path}`` and return parsed JSON, or ``None`` on failure.

    Retries once on HTTP 429 after a short pause.
    """
    key = get_nyt_api_key()
    if not key:
        logger.warning("nyt_api_key_missing")
        return None

    query = {"api-key": key, **(params or {})}
    url = f"{NYT_API_BASE}{path}"

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=query)
            if response.status_code == 429 and attempt == 0:
                logger.warning("nyt_api_rate_limited", path=path)
                await asyncio.sleep(6.0)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "nyt_api_http_error", path=path, status=exc.response.status_code
            )
            return None
        except (httpx.RequestError, ValueError) as exc:
            logger.warning("nyt_api_request_error", path=path, error=str(exc))
            return None
    return None


async def fetch_list_names() -> list[dict[str, Any]]:
    """Return the catalogue of Best Sellers lists (for the Settings picker)."""
    data = await _get_json("/lists/names.json")
    if not data:
        return []
    results = data.get("results") or []
    return [
        {
            "list_name": item.get("list_name"),
            "list_name_encoded": item.get("list_name_encoded"),
            "display_name": item.get("display_name") or item.get("list_name"),
            "updated": item.get("updated"),
            "oldest_published_date": item.get("oldest_published_date"),
            "newest_published_date": item.get("newest_published_date"),
        }
        for item in results
        if item.get("list_name_encoded")
    ]


def _catalog_from_lists(lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract the {name, slug, updated} catalogue from list objects."""
    return [
        {
            "list_name": item.get("list_name"),
            "list_name_encoded": item.get("list_name_encoded"),
            "display_name": item.get("display_name") or item.get("list_name"),
            "updated": item.get("updated"),
            "oldest_published_date": item.get("oldest_published_date"),
            "newest_published_date": item.get("newest_published_date"),
        }
        for item in lists
        if item.get("list_name_encoded")
    ]


async def fetch_list_catalog() -> list[dict[str, Any]]:
    """Return the catalogue of selectable lists.

    Tries ``names.json`` first; if that fails (commonly a transient 429), derives
    the catalogue from the ``full-overview`` payload instead, which lists every
    currently-published list with its display name and update cadence.
    """
    names = await fetch_list_names()
    if names:
        return names
    return _catalog_from_lists(await fetch_full_overview())


async def fetch_full_overview() -> list[dict[str, Any]]:
    """Return every current Best Sellers list with all of its books.

    Uses the (undocumented but stable) ``full-overview`` endpoint, which returns
    ~15 books per list in a single request.  Falls back to ``overview`` (top 5
    per list) if that endpoint is unavailable.
    """
    data = await _get_json("/lists/full-overview.json")
    if not data:
        data = await _get_json("/lists/overview.json")
    if not data:
        return []
    return (data.get("results") or {}).get("lists") or []
