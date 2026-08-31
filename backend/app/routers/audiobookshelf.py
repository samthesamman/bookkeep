"""Audiobookshelf API integration for audiobook availability"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
import asyncio
import os
import httpx
import structlog

from app import database, models, schemas
from app.auth import get_current_user, require_admin

logger = structlog.get_logger()

router = APIRouter()

# Metadata provider Audiobookshelf uses when asked to quick-match an item
# (POST /api/items/:id/match, `{"provider": ...}`). Override with the
# `audiobookshelf_match_provider` app setting or the AUDIOBOOKSHELF_MATCH_PROVIDER
# env var. Audiobookshelf falls back to the library's own provider when omitted.
DEFAULT_ABS_MATCH_PROVIDER = "audible"


def get_audiobookshelf_match_provider(db: Session) -> str:
    """Resolve the metadata provider to pass to Audiobookshelf's match endpoint."""
    row = (
        db.query(models.AppSettings)
        .filter(models.AppSettings.key == "audiobookshelf_match_provider")
        .first()
    )
    if row and row.value and row.value.strip():
        return row.value.strip()
    return os.getenv("AUDIOBOOKSHELF_MATCH_PROVIDER", DEFAULT_ABS_MATCH_PROVIDER)


def get_default_audiobookshelf_server(db: Session) -> Optional[models.AudiobookshelfServer]:
    """Get the default Audiobookshelf server"""
    server = db.query(models.AudiobookshelfServer).filter(
        models.AudiobookshelfServer.is_default == True
    ).first()

    if not server:
        # Fall back to first server if no default
        server = db.query(models.AudiobookshelfServer).first()

    return server


def _auth_headers(server: models.AudiobookshelfServer) -> Dict[str, str]:
    """Build authorization headers for Audiobookshelf API"""
    return {"Authorization": f"Bearer {server.api_key}"}


async def get_audiobookshelf_libraries(server: models.AudiobookshelfServer) -> List[Dict[str, Any]]:
    """Get all libraries from Audiobookshelf, filtering out podcast libraries"""
    url = f"{server.url.rstrip('/')}/api/libraries"

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
            response = await client.get(url, headers=_auth_headers(server))

            if response.status_code == 200:
                data = response.json()
                libraries = data.get("libraries", data) if isinstance(data, dict) else data
                # Filter out podcast libraries
                return [
                    lib for lib in libraries
                    if lib.get("mediaType") != "podcast"
                ]
            else:
                logger.error("audiobookshelf_libraries_failed",
                             status_code=response.status_code,
                             response=response.text[:200])
                return []
    except httpx.RequestError as e:
        logger.error("audiobookshelf_libraries_error", error=str(e))
        return []


async def _book_library_ids(server: models.AudiobookshelfServer) -> List[str]:
    """The library ids to act on: server.library_id if pinned, else all book libraries."""
    if server.library_id:
        return [server.library_id]
    return [lib["id"] for lib in await get_audiobookshelf_libraries(server) if lib.get("id")]


async def trigger_audiobookshelf_scan(server: models.AudiobookshelfServer) -> bool:
    """Ask Audiobookshelf to rescan its book libraries for newly added files.

    Scans ``server.library_id`` when set, otherwise every non-podcast library.
    Best effort — the Audiobookshelf API key must belong to an admin/root user
    (``POST /api/libraries/:id/scan`` returns 403 otherwise). Returns True if at
    least one library scan was accepted.
    """
    library_ids = await _book_library_ids(server)
    if not library_ids:
        logger.warning("audiobookshelf_scan_no_libraries", server=server.name)
        return False

    triggered = 0
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=False) as client:
        for lib_id in library_ids:
            url = f"{server.url.rstrip('/')}/api/libraries/{lib_id}/scan"
            try:
                response = await client.post(url, headers=_auth_headers(server))
            except httpx.RequestError as e:
                logger.warning("audiobookshelf_scan_error", library_id=lib_id, url=url, error=str(e))
                continue
            if response.status_code in (200, 202):
                triggered += 1
                logger.info("audiobookshelf_scan_started", library_id=lib_id, status_code=response.status_code)
            else:
                logger.warning(
                    "audiobookshelf_scan_failed",
                    library_id=lib_id,
                    url=url,
                    status_code=response.status_code,
                    response=response.text[:200],
                    hint="API key must be an admin/root user",
                )

    return triggered > 0


async def get_audiobookshelf_max_added_at(server: models.AudiobookshelfServer) -> int:
    """Current newest ``addedAt`` (ms epoch) across the book libraries.

    Captured *before* a scan as a baseline so the follow-up can tell which items
    the scan actually brought in — using Audiobookshelf's own clock, so host
    clock skew doesn't matter.
    """
    newest = 0
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
            for lib_id in await _book_library_ids(server):
                url = f"{server.url.rstrip('/')}/api/libraries/{lib_id}/items"
                response = await client.get(
                    url,
                    headers=_auth_headers(server),
                    params={"sort": "addedAt", "desc": "1", "minified": "1", "limit": 1},
                )
                if response.status_code == 200:
                    results = response.json().get("results", [])
                    if results:
                        newest = max(newest, results[0].get("addedAt") or 0)
    except (httpx.RequestError, ValueError) as e:
        logger.warning("audiobookshelf_baseline_error", error=str(e))
    return newest


async def get_recently_added_audiobookshelf_items(
    server: models.AudiobookshelfServer,
    since_ms: int,
    *,
    per_library_limit: int = 50,
) -> List[Dict[str, Any]]:
    """Library items with ``addedAt`` strictly greater than ``since_ms``, newest first."""
    recent: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=False) as client:
            for lib_id in await _book_library_ids(server):
                url = f"{server.url.rstrip('/')}/api/libraries/{lib_id}/items"
                response = await client.get(
                    url,
                    headers=_auth_headers(server),
                    params={
                        "sort": "addedAt",
                        "desc": "1",
                        "minified": "1",
                        "limit": per_library_limit,
                    },
                )
                if response.status_code != 200:
                    logger.warning(
                        "audiobookshelf_recent_items_failed",
                        library_id=lib_id, status_code=response.status_code,
                    )
                    continue
                for item in response.json().get("results", []):
                    if (item.get("addedAt") or 0) > since_ms:
                        recent.append(item)
                    else:
                        break  # sorted desc — everything after is older
    except (httpx.RequestError, ValueError) as e:
        logger.warning("audiobookshelf_recent_items_error", error=str(e))
    return recent


async def match_audiobookshelf_item(
    server: models.AudiobookshelfServer,
    item_id: str,
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    isbn: Optional[str] = None,
    provider: Optional[str] = None,
    override_details: bool = False,
) -> bool:
    """Trigger a metadata quick-match for one Audiobookshelf library item.

    Audiobookshelf re-queries its configured metadata provider and updates the
    item's details/cover. ``provider`` defaults to the library's own setting.
    Returns True when Audiobookshelf reports the item was updated.
    """
    url = f"{server.url.rstrip('/')}/api/items/{item_id}/match"
    payload: Dict[str, Any] = {"overrideDetails": override_details}
    if provider:
        payload["provider"] = provider
    if title:
        payload["title"] = title
    if author:
        payload["author"] = author
    if isbn:
        payload["isbn"] = isbn

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=False) as client:
            response = await client.post(url, headers=_auth_headers(server), json=payload)
        if response.status_code in (200, 202):
            body = response.json() if response.content else {}
            if isinstance(body, dict) and body.get("warning"):
                logger.info("audiobookshelf_match_no_result", item_id=item_id, warning=body["warning"])
                return False
            logger.info("audiobookshelf_match_ok", item_id=item_id)
            return True
        logger.warning(
            "audiobookshelf_match_failed",
            item_id=item_id,
            status_code=response.status_code,
            response=response.text[:200],
        )
    except (httpx.RequestError, ValueError) as e:
        logger.warning("audiobookshelf_match_error", item_id=item_id, error=str(e))
    return False


async def link_and_match_new_audiobook(
    server_id: int,
    book_id: int,
    baseline_added_at_ms: int,
    *,
    attempts: int = 6,
    delay_seconds: float = 5.0,
) -> None:
    """After a scan, quick-match every Audiobookshelf item added since
    ``baseline_added_at_ms`` and link this download's book to the new item.

    The match runs entirely on the Audiobookshelf server against its own metadata
    provider — we send no book metadata, so nothing here can affect our records.
    Polls the ``addedAt``-sorted, minified item list and stops once a poll turns
    up nothing new. ``sync_from_audiobookshelf`` is the backstop.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        server = db.query(models.AudiobookshelfServer).filter(
            models.AudiobookshelfServer.id == server_id
        ).first()
        book = db.query(models.Book).filter(models.Book.id == book_id).first()
        if not server or not book:
            return

        provider = get_audiobookshelf_match_provider(db)

        # id -> item, for every item added since the scan started.
        new_items: Dict[str, Dict[str, Any]] = {}
        for _ in range(attempts):
            await asyncio.sleep(delay_seconds)
            try:
                recent = await get_recently_added_audiobookshelf_items(server, baseline_added_at_ms)
            except Exception as e:  # pragma: no cover - network
                logger.warning("audiobookshelf_recent_poll_error", book_id=book_id, error=str(e))
                continue

            fresh = [it for it in recent if it.get("id") and it["id"] not in new_items]
            for item in fresh:
                new_items[item["id"]] = item
                await match_audiobookshelf_item(server, item["id"], provider=provider)
                await asyncio.sleep(1.0)

            # Nothing new after we've already seen something → the scan settled.
            if new_items and not fresh:
                break

        # Link the book: the new item that matches by name, else the sole new one.
        if not book.audiobookshelf_id and new_items:
            target = next(
                (iid for iid, it in new_items.items() if match_book_to_abs_item(book, it)),
                None,
            ) or (next(iter(new_items)) if len(new_items) == 1 else None)
            if target:
                book.audiobookshelf_id = target
                book.audiobook_available = True
                db.commit()
                logger.info(
                    "audiobookshelf_new_item_linked", book_id=book_id, item_id=target
                )

        logger.info(
            "audiobookshelf_recent_match_done",
            book_id=book_id, new_items=len(new_items),
            linked=bool(book.audiobookshelf_id),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.error("audiobookshelf_link_and_match_error", book_id=book_id, error=str(e))
    finally:
        db.close()


async def get_audiobookshelf_library_items(
    server: models.AudiobookshelfServer,
    library_id: str,
) -> List[Dict[str, Any]]:
    """Get all items from a specific Audiobookshelf library with pagination"""
    base_url = f"{server.url.rstrip('/')}/api/libraries/{library_id}/items"
    all_items = []
    page = 0
    limit = 100

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=False) as client:
            while True:
                response = await client.get(
                    base_url,
                    headers=_auth_headers(server),
                    params={"minified": "1", "limit": limit, "page": page},
                )

                if response.status_code != 200:
                    logger.error("audiobookshelf_items_failed",
                                 library_id=library_id,
                                 status_code=response.status_code)
                    break

                data = response.json()
                results = data.get("results", [])
                all_items.extend(results)

                total = data.get("total", 0)
                if len(all_items) >= total or not results:
                    break

                page += 1

        logger.info("audiobookshelf_items_fetched",
                     library_id=library_id,
                     count=len(all_items))
        return all_items
    except httpx.RequestError as e:
        logger.error("audiobookshelf_items_error",
                     library_id=library_id,
                     error=str(e))
        return []


def _norm_for_match(value: Optional[str]) -> str:
    """Lowercase, drop punctuation, collapse whitespace — so a title stripped of
    ``:`` / ``?`` for an on-disk folder name still matches the real title."""
    text = (value or "").lower()
    for ch in ",.:;!?'\"()[]{}-":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def match_book_to_abs_item(book: models.Book, item: Dict[str, Any]) -> bool:
    """Check if an Audiobookshelf item matches a Book record by ISBN or title+author"""
    metadata = (item.get("media") or {}).get("metadata") or {}

    item_isbn = metadata.get("isbn")
    if item_isbn and book.isbn and item_isbn == book.isbn:
        return True

    item_title = _norm_for_match(metadata.get("title"))
    item_author = _norm_for_match(metadata.get("authorName"))
    book_title = _norm_for_match(book.title)
    book_author = _norm_for_match(book.author)
    if not (item_title and item_author and book_title and book_author):
        return False

    # Titles match outright, or one is the other minus a trailing subtitle.
    # The subtitle tolerance needs a substantial shared stem so single-word
    # titles don't collide ("Dune" vs "Dune Messiah", "It" vs "It Ends...").
    shorter = min(item_title, book_title, key=len)
    title_ok = item_title == book_title or (
        len(shorter) >= 10
        and (
            book_title.startswith(item_title + " ")
            or item_title.startswith(book_title + " ")
        )
    )
    # Authors match outright, or share a surname ("Gil Duran" vs "Duran, Gil").
    author_ok = (
        item_author == book_author
        or book_author.split()[-1] in item_author.split()
        or item_author.split()[-1] in book_author.split()
    )
    return title_ok and author_ok


async def search_audiobookshelf_items(
    server: models.AudiobookshelfServer,
    query: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search for items in Audiobookshelf by title/author (targeted, not full scan)"""
    library_ids = [server.library_id] if server.library_id else [
        lib["id"] for lib in await get_audiobookshelf_libraries(server)
    ]
    results = []
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
            for lib_id in library_ids:
                url = f"{server.url.rstrip('/')}/api/libraries/{lib_id}/search"
                response = await client.get(
                    url,
                    headers=_auth_headers(server),
                    params={"q": query, "limit": limit},
                )
                if response.status_code == 200:
                    data = response.json()
                    for entry in data.get("book", []):
                        lib_item = entry.get("libraryItem")
                        if lib_item:
                            results.append(lib_item)
    except httpx.RequestError as e:
        logger.error("audiobookshelf_search_error", query=query, error=str(e))
    return results


async def get_all_audiobookshelf_items(server: models.AudiobookshelfServer) -> List[Dict[str, Any]]:
    """
    Get all audiobook items from Audiobookshelf.
    If server.library_id is set, scan only that library; otherwise scan all book-type libraries.
    """
    if server.library_id:
        return await get_audiobookshelf_library_items(server, server.library_id)

    libraries = await get_audiobookshelf_libraries(server)
    all_items = []

    for lib in libraries:
        lib_id = lib.get("id")
        if lib_id:
            items = await get_audiobookshelf_library_items(server, lib_id)
            all_items.extend(items)

    return all_items


# API Endpoints


def _normalize_abs_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an Audiobookshelf library item into the shape the UI needs."""
    media = item.get("media", {}) or {}
    metadata = media.get("metadata", {}) or {}
    return {
        "id": item.get("id"),
        "title": metadata.get("title") or "Unknown Title",
        "author": metadata.get("authorName") or None,
        "narrator": metadata.get("narratorName") or None,
        "series": metadata.get("seriesName") or None,
        "isbn": metadata.get("isbn") or None,
        "published_year": metadata.get("publishedYear") or None,
        "description": metadata.get("description") or None,
        "duration_seconds": media.get("duration"),
        "num_tracks": media.get("numTracks"),
        "has_cover": bool(media.get("coverPath")),
        "added_at": item.get("addedAt"),
        # Filled in from the local catalog when we already know the book.
        "hardcover_id": None,
        "book_id": None,
        "ebook_available": False,
        "audiobook_available": False,
    }


def _match_local_book(entry: Dict[str, Any], db: Session) -> Optional[models.Book]:
    """Find the catalog Book for an Audiobookshelf item (abs id → ISBN → title+author)."""
    book = None
    if entry.get("id"):
        book = db.query(models.Book).filter(
            models.Book.audiobookshelf_id == entry["id"]
        ).first()
    if not book and entry.get("isbn"):
        book = db.query(models.Book).filter(models.Book.isbn == entry["isbn"]).first()
    if not book and entry.get("title") and entry.get("author"):
        book = db.query(models.Book).filter(
            func.lower(models.Book.title) == entry["title"].lower(),
            func.lower(models.Book.author) == entry["author"].lower(),
        ).first()
    return book


# NOTE: keep the /library/* routes above the /{server_id} routes so
# "library" isn't parsed as a server id.
@router.get("/library/items")
async def list_library_items(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List every item in the default Audiobookshelf library (any signed-in user)."""
    server = get_default_audiobookshelf_server(db)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf is not configured",
        )

    items = await get_all_audiobookshelf_items(server)
    normalized = [_normalize_abs_item(item) for item in items if item.get("id")]

    # Attach catalog info so the UI can deep-link straight to the book page for
    # items we've already matched (the rest resolve lazily on click). Matching
    # is batched: one pass over the catalog builds abs-id / ISBN / title+author
    # indexes instead of querying per item.
    by_abs_id: Dict[str, models.Book] = {}
    by_isbn: Dict[str, models.Book] = {}
    by_title_author: Dict[tuple, models.Book] = {}
    for book in db.query(
        models.Book.id,
        models.Book.hardcover_id,
        models.Book.title,
        models.Book.author,
        models.Book.isbn,
        models.Book.audiobookshelf_id,
        models.Book.ebook_available,
        models.Book.audiobook_available,
    ):
        if book.audiobookshelf_id:
            by_abs_id[book.audiobookshelf_id] = book
        if book.isbn:
            by_isbn.setdefault(book.isbn, book)
        if book.title and book.author:
            by_title_author.setdefault(
                (book.title.lower(), book.author.lower()), book
            )

    for entry in normalized:
        book = (
            by_abs_id.get(entry["id"])
            or (by_isbn.get(entry["isbn"]) if entry["isbn"] else None)
            or (
                by_title_author.get((entry["title"].lower(), entry["author"].lower()))
                if entry["title"] and entry["author"]
                else None
            )
        )
        if book:
            entry["hardcover_id"] = book.hardcover_id
            entry["book_id"] = book.id
            entry["ebook_available"] = bool(book.ebook_available)
            entry["audiobook_available"] = bool(book.audiobook_available)

    normalized.sort(key=lambda i: (i["title"] or "").lower())
    return normalized


async def _fetch_abs_item(server: models.AudiobookshelfServer, item_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single expanded library item from Audiobookshelf."""
    url = f"{server.url.rstrip('/')}/api/items/{item_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
            response = await client.get(url, headers=_auth_headers(server))
    except httpx.RequestError as e:
        logger.error("audiobookshelf_item_fetch_error", item_id=item_id, error=str(e))
        return None
    if response.status_code != 200:
        return None
    return response.json()


def _link_book_to_calibre(db: Session, book: models.Book) -> None:
    """Best-effort: link ``book`` to a matching Calibre library book.

    Keeps the eBook (Calibre) and audiobook (Audiobookshelf) sides of the same
    title on one record, so the book page shows the Calibre files / "email to
    myself" and CalibreBookDetails redirects to the shared page. No-op when
    there's no Calibre library, no match, or the book is already linked.
    """
    try:
        from app.routers.calibre import get_active_library_path
        from app.services import calibre_service, calibre_link_service

        library_path = get_active_library_path(db)
        if not library_path:
            return

        already = db.query(models.CalibreBookLink).filter(
            models.CalibreBookLink.book_id == book.id
        ).first()
        if already:
            return

        match_id = calibre_service.find_book_match(
            library_path, book.title, book.author, book.isbn
        )
        if not match_id:
            return

        calibre_link_service.upsert_link(
            db,
            calibre_book_id=match_id,
            book_id=book.id,
            source="fuzzy",
            confirmed=False,
            calibre_title=book.title,
            commit=False,
        )

        formats = calibre_service.formats_for_ids(library_path, [match_id]).get(match_id, [])
        kinds = {calibre_service.classify_format(f) for f in formats}
        if "ebook" in kinds:
            book.ebook_available = True
        if "audiobook" in kinds:
            book.audiobook_available = True
    except Exception as exc:  # never let linking break the resolve
        logger.warning("audiobookshelf_resolve_calibre_link_failed", book_id=book.id, error=str(exc))


@router.get("/library/items/{item_id}/resolve")
async def resolve_library_item(
    item_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Resolve an Audiobookshelf item to a catalog book so the UI can open its
    details page. Reuses an existing Book, or looks the title up on Hardcover
    and creates one. The resulting book is linked to this abs item and marked
    audiobook-available.
    """
    from app.routers.hardcover import lookup_book_by_title_author, _save_book_to_db

    server = get_default_audiobookshelf_server(db)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf is not configured",
        )

    # Fast path: already linked.
    book = db.query(models.Book).filter(
        models.Book.audiobookshelf_id == item_id
    ).first()

    if not book:
        raw = await _fetch_abs_item(server, item_id)
        if not raw:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        entry = _normalize_abs_item(raw)
        book = _match_local_book(entry, db)

        if not book:
            hardcover_data = await lookup_book_by_title_author(
                entry["title"], entry.get("author"), db
            )
            if not hardcover_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Couldn't match this audiobook to a Hardcover book",
                )
            book = _save_book_to_db(hardcover_data, db)
            if not book:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to save the matched book",
                )

    book.audiobookshelf_id = item_id
    book.audiobook_available = True
    _link_book_to_calibre(db, book)
    try:
        db.commit()
        db.refresh(book)
    except Exception as e:
        logger.warning("audiobookshelf_resolve_commit_failed", item_id=item_id, error=str(e))
        db.rollback()

    if not book.hardcover_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Matched book has no Hardcover ID",
        )

    return {
        "hardcover_id": book.hardcover_id,
        "book_id": book.id,
        "ebook_available": bool(book.ebook_available),
        "audiobook_available": bool(book.audiobook_available),
    }


@router.get("/library/items/{item_id}/cover")
async def get_library_item_cover(
    item_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Proxy an Audiobookshelf item cover so the browser never sees the API key."""
    server = get_default_audiobookshelf_server(db)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf is not configured",
        )

    url = f"{server.url.rstrip('/')}/api/items/{item_id}/cover"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
            response = await client.get(url, headers=_auth_headers(server))
    except httpx.RequestError as e:
        logger.error("audiobookshelf_cover_error", item_id=item_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Audiobookshelf unreachable")

    if response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover not found")

    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/", response_model=List[schemas.AudiobookshelfServerResponse])
async def list_servers(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin)
):
    """List all Audiobookshelf servers (admin only)"""
    servers = db.query(models.AudiobookshelfServer).all()
    return servers


@router.post("/", response_model=schemas.AudiobookshelfServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    server: schemas.AudiobookshelfServerCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin)
):
    """Create a new Audiobookshelf server configuration (admin only)"""
    # If this is set as default, unset other defaults
    if server.is_default:
        db.query(models.AudiobookshelfServer).update({"is_default": False})

    db_server = models.AudiobookshelfServer(
        name=server.name,
        url=server.url,
        api_key=server.api_key,
        is_default=server.is_default,
        library_id=server.library_id,
    )
    db.add(db_server)
    db.commit()
    db.refresh(db_server)

    logger.info("audiobookshelf_server_created", server_id=db_server.id, name=db_server.name)
    return db_server


@router.get("/{server_id}", response_model=schemas.AudiobookshelfServerResponse)
async def get_server(
    server_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin)
):
    """Get a specific Audiobookshelf server (admin only)"""
    server = db.query(models.AudiobookshelfServer).filter(
        models.AudiobookshelfServer.id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf server not found"
        )

    return server


@router.put("/{server_id}", response_model=schemas.AudiobookshelfServerResponse)
async def update_server(
    server_id: int,
    server_update: schemas.AudiobookshelfServerUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin)
):
    """Update an Audiobookshelf server configuration (admin only)"""
    server = db.query(models.AudiobookshelfServer).filter(
        models.AudiobookshelfServer.id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf server not found"
        )

    # If setting as default, unset others
    if server_update.is_default:
        db.query(models.AudiobookshelfServer).filter(
            models.AudiobookshelfServer.id != server_id
        ).update({"is_default": False})

    # Update fields (skip api_key if not provided or empty)
    update_data = server_update.model_dump(exclude_unset=True)
    if "api_key" in update_data and not update_data["api_key"]:
        del update_data["api_key"]
    for field, value in update_data.items():
        setattr(server, field, value)

    db.add(server)
    db.commit()
    db.refresh(server)

    logger.info("audiobookshelf_server_updated", server_id=server_id)
    return server


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin)
):
    """Delete an Audiobookshelf server configuration (admin only)"""
    server = db.query(models.AudiobookshelfServer).filter(
        models.AudiobookshelfServer.id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf server not found"
        )

    db.delete(server)
    db.commit()

    logger.info("audiobookshelf_server_deleted", server_id=server_id)
    return None


@router.post("/test", response_model=schemas.AudiobookshelfTestConnectionResponse)
async def test_connection(
    request: schemas.AudiobookshelfTestConnectionRequest,
    current_user: models.User = Depends(require_admin)
):
    """Test connection to an Audiobookshelf server (admin only)"""
    url = f"{request.url.rstrip('/')}/api/authorize"
    headers = {"Authorization": f"Bearer {request.api_key}"}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=False) as client:
            response = await client.post(url, headers=headers)

            logger.debug("audiobookshelf_test_connection",
                        status_code=response.status_code)

            if response.status_code == 200:
                # Get libraries
                lib_url = f"{request.url.rstrip('/')}/api/libraries"
                lib_response = await client.get(lib_url, headers=headers)
                libraries = []

                if lib_response.status_code == 200:
                    data = lib_response.json()
                    raw_libs = data.get("libraries", data) if isinstance(data, dict) else data
                    libraries = [
                        {"id": lib.get("id"), "name": lib.get("name"), "mediaType": lib.get("mediaType")}
                        for lib in raw_libs
                        if lib.get("mediaType") != "podcast"
                    ]

                return schemas.AudiobookshelfTestConnectionResponse(
                    success=True,
                    libraries=libraries
                )
            else:
                return schemas.AudiobookshelfTestConnectionResponse(
                    success=False,
                    error=f"Authentication failed (HTTP {response.status_code}). Check your URL and API key."
                )
    except httpx.RequestError as e:
        return schemas.AudiobookshelfTestConnectionResponse(
            success=False,
            error=f"Connection error: {str(e)}"
        )
    except Exception as e:
        return schemas.AudiobookshelfTestConnectionResponse(
            success=False,
            error=f"Error: {str(e)}"
        )


@router.get("/{server_id}/items")
async def get_items(
    server_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(require_admin)
):
    """Get all items from an Audiobookshelf server (admin only)"""
    server = db.query(models.AudiobookshelfServer).filter(
        models.AudiobookshelfServer.id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf server not found"
        )

    items = await get_all_audiobookshelf_items(server)
    return items


@router.get("/{server_id}/check/{hardcover_id}")
async def check_book_availability(
    server_id: int,
    hardcover_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Check if a book is available in Audiobookshelf by Hardcover ID"""
    server = db.query(models.AudiobookshelfServer).filter(
        models.AudiobookshelfServer.id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audiobookshelf server not found"
        )

    # Check if we already have a linked audiobookshelf_id for this book
    book = db.query(models.Book).filter(
        models.Book.hardcover_id == hardcover_id
    ).first()

    if book and book.audiobookshelf_id:
        return {"available": True, "source": "cached_link"}

    if not book:
        return {"available": False}

    # Targeted search instead of fetching all items
    search_query = book.title or ""
    if book.author:
        search_query = f"{search_query} {book.author}"

    items = await search_audiobookshelf_items(server, search_query.strip())

    for item in items:
        if match_book_to_abs_item(book, item):
            book.audiobookshelf_id = item.get("id")
            db.commit()
            return {"available": True, "item_id": item.get("id"), "source": "search_match"}

    return {"available": False}
