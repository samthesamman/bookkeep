"""Discover page endpoints — NYT Best Sellers, with book data from Hardcover.

The Best Sellers lists come from the NYT Books API; the actual book records
(cover, rating, series, request/availability state) are resolved against
Hardcover by ISBN so the existing request flow keeps working.  Books with no
Hardcover match are dropped.  The whole payload is cached for 24h and refreshed
by the ``refresh_nyt_bestsellers`` scheduled job.
"""
import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import cache, database, models, schemas
from app.auth import require_admin
from app.routers.hardcover import (
    _enrich_books_with_availability,
    _normalize_isbn,
    _parse_hardcover_book,
    _save_book_to_db,
    resolve_books_by_isbns,
)
from app.routers.settings import get_setting_value, set_setting_value
from app.services.nyt_bestsellers import (
    DEFAULT_LIST_SLUGS,
    NYT_ATTRIBUTION,
    fetch_full_overview,
    fetch_list_names,
    get_nyt_api_key,
)

logger = structlog.get_logger()

router = APIRouter()

BESTSELLERS_CACHE_KEY = "nyt_bestsellers:v1"
SELECTED_LISTS_SETTING = "nyt_bestseller_lists"


def get_selected_list_slugs(db: Session) -> list[str]:
    """Return the admin-configured list slugs, or the default headliner set."""
    raw = get_setting_value(db, SELECTED_LISTS_SETTING)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                slugs = [str(s) for s in parsed if s]
                if slugs:
                    return slugs
        except (ValueError, TypeError):
            logger.warning("nyt_selected_lists_parse_failed", raw=raw)
    return list(DEFAULT_LIST_SLUGS)


async def build_bestsellers_payload(db: Session) -> schemas.NYTBestsellersResponse:
    """Fetch NYT lists, resolve books via Hardcover, and cache the result."""
    empty = schemas.NYTBestsellersResponse(
        lists=[], attribution=NYT_ATTRIBUTION,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    if not get_nyt_api_key():
        return empty

    selected = get_selected_list_slugs(db)
    selected_set = set(selected)
    all_lists = await fetch_full_overview()
    if not all_lists:
        return empty

    lists_by_slug = {
        lst.get("list_name_encoded"): lst
        for lst in all_lists
        if lst.get("list_name_encoded") in selected_set
    }

    # Collect every ISBN across the selected lists for one batched Hardcover lookup.
    all_isbns: list[str] = []
    for lst in lists_by_slug.values():
        for book in lst.get("books") or []:
            isbn = book.get("primary_isbn13") or book.get("primary_isbn10")
            if isbn:
                all_isbns.append(isbn)

    resolved = await resolve_books_by_isbns(all_isbns, db)

    # Persist matched books so requests / availability enrichment work.
    saved_ids: set[int] = set()
    for book_data in resolved.values():
        if book_data.get("id") in saved_ids:
            continue
        _save_book_to_db(book_data, db)
        saved_ids.add(book_data["id"])

    out_lists: list[schemas.NYTBestsellerList] = []
    for slug in selected:  # preserve admin-configured order
        lst = lists_by_slug.get(slug)
        if not lst:
            continue
        books: list[schemas.HardcoverBook] = []
        seen: set[int] = set()
        for nyt_book in lst.get("books") or []:
            isbn = nyt_book.get("primary_isbn13") or nyt_book.get("primary_isbn10")
            book_data = resolved.get(_normalize_isbn(isbn) or "")
            if not book_data or book_data["id"] in seen:
                continue
            seen.add(book_data["id"])
            books.append(_parse_hardcover_book(book_data))
        if not books:
            continue
        books = _enrich_books_with_availability(books, db)
        out_lists.append(schemas.NYTBestsellerList(
            list_name=lst.get("list_name") or slug,
            list_name_encoded=slug,
            updated=lst.get("updated"),
            books=books,
        ))

    payload = schemas.NYTBestsellersResponse(
        lists=out_lists,
        attribution=NYT_ATTRIBUTION,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    await cache.set_cached(
        BESTSELLERS_CACHE_KEY, payload.model_dump(),
        ttl=cache.CACHE_TTL["nyt_bestsellers"],
    )
    return payload


@router.get("/status", response_model=schemas.DiscoverStatusResponse)
async def discover_status():
    """Whether the NYT Books API key is configured (any user)."""
    return schemas.DiscoverStatusResponse(has_nyt_key=bool(get_nyt_api_key()))


@router.get("/bestsellers", response_model=schemas.NYTBestsellersResponse)
async def get_bestsellers(db: Session = Depends(database.get_db)):
    """NYT Best Sellers for the configured lists — Cache → NYT + Hardcover."""
    cached = await cache.get_cached(BESTSELLERS_CACHE_KEY)
    if cached is not None:
        return schemas.NYTBestsellersResponse(**cached)
    return await build_bestsellers_payload(db)


@router.get("/nyt-lists", response_model=schemas.NYTListsResponse)
async def get_nyt_lists(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    """Catalogue of NYT lists plus the current selection (admin only)."""
    available = await fetch_list_names()
    return schemas.NYTListsResponse(
        available=[schemas.NYTListName(**item) for item in available],
        selected=get_selected_list_slugs(db),
        has_nyt_key=bool(get_nyt_api_key()),
    )


@router.put("/nyt-lists", response_model=schemas.NYTListsResponse)
async def set_nyt_lists(
    update: schemas.NYTListsUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin),
):
    """Set which NYT lists appear on Discover, in order (admin only)."""
    available = await fetch_list_names()
    if not available:
        raise HTTPException(
            status_code=400,
            detail="Could not load NYT lists. Check that NYT_BOOKS_API_KEY is set and valid.",
        )
    valid = {item["list_name_encoded"] for item in available}
    # Keep only known slugs, preserving the submitted order and dropping dupes.
    seen: set[str] = set()
    slugs = [
        s for s in update.lists
        if s in valid and not (s in seen or seen.add(s))
    ]
    set_setting_value(db, SELECTED_LISTS_SETTING, json.dumps(slugs))
    await cache.delete_cached(BESTSELLERS_CACHE_KEY)

    return schemas.NYTListsResponse(
        available=[schemas.NYTListName(**item) for item in available],
        selected=slugs,
        has_nyt_key=bool(get_nyt_api_key()),
    )
