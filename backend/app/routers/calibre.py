"""API for browsing a local Calibre library (see app.services.calibre_service)."""
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user, require_admin
from app.database import get_db
from app.services import calibre_service, calibre_link_service

logger = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class CalibreSettingsUpdate(BaseModel):
    library_path: Optional[str] = None
    enabled: bool = False


class CalibreSettingsResponse(BaseModel):
    id: int
    library_path: Optional[str]
    enabled: bool
    valid: bool
    book_count: Optional[int] = None
    error: Optional[str] = None


class CalibreTestRequest(BaseModel):
    library_path: str


class CalibreTestResponse(BaseModel):
    success: bool
    book_count: Optional[int] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_or_create(db: Session) -> models.CalibreSettings:
    row = db.query(models.CalibreSettings).first()
    if row is None:
        row = models.CalibreSettings(library_path=None, enabled=False)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _probe(library_path: Optional[str]) -> tuple[bool, Optional[int], Optional[str]]:
    if not library_path:
        return False, None, None
    try:
        stats = calibre_service.library_stats(library_path)
        return True, stats["book_count"], None
    except calibre_service.CalibreError as exc:
        return False, None, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("calibre_probe_failed", error=str(exc))
        return False, None, str(exc)


def _require_library(db: Session) -> str:
    row = db.query(models.CalibreSettings).first()
    if row is None or not row.enabled or not row.library_path:
        raise HTTPException(status_code=400, detail="Calibre library is not configured")
    try:
        calibre_service.resolve_db_path(row.library_path)
    except calibre_service.CalibreError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return row.library_path


OVERLAY_ENABLED_KEY = "calibre_overlay_enabled"
OVERLAY_PREFER_LOCAL_KEY = "calibre_overlay_prefer_local"


def _bool_setting(db: Session, key: str, default: bool) -> bool:
    row = db.query(models.AppSettings).filter(models.AppSettings.key == key).first()
    if row is None or row.value is None:
        return default
    return str(row.value).lower() not in ("false", "0", "")


def _overlay_config(db: Session) -> tuple[bool, bool]:
    """Return (overlay_enabled, prefer_local)."""
    return (
        _bool_setting(db, OVERLAY_ENABLED_KEY, True),
        _bool_setting(db, OVERLAY_PREFER_LOCAL_KEY, False),
    )


def get_active_library_path(db: Session) -> Optional[str]:
    """Return the configured, enabled, readable Calibre library path, or None.

    Non-raising counterpart of ``_require_library`` for use by background jobs.
    """
    row = db.query(models.CalibreSettings).first()
    if row is None or not row.enabled or not row.library_path:
        return None
    try:
        calibre_service.resolve_db_path(row.library_path)
    except calibre_service.CalibreError:
        return None
    return row.library_path


# ---------------------------------------------------------------------------
# Settings (admin only)
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=CalibreSettingsResponse)
async def get_settings(
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    row = _get_or_create(db)
    valid, count, error = _probe(row.library_path)
    return CalibreSettingsResponse(
        id=row.id,
        library_path=row.library_path,
        enabled=row.enabled,
        valid=valid,
        book_count=count,
        error=error,
    )


@router.put("/settings", response_model=CalibreSettingsResponse)
async def update_settings(
    data: CalibreSettingsUpdate,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    row = _get_or_create(db)
    row.library_path = (data.library_path or "").strip() or None
    row.enabled = data.enabled
    db.commit()
    db.refresh(row)

    valid, count, error = _probe(row.library_path)
    logger.info("calibre_settings_updated", enabled=row.enabled, valid=valid)
    return CalibreSettingsResponse(
        id=row.id,
        library_path=row.library_path,
        enabled=row.enabled,
        valid=valid,
        book_count=count,
        error=error,
    )


@router.post("/test", response_model=CalibreTestResponse)
async def test_library(
    data: CalibreTestRequest,
    _: models.User = Depends(require_admin),
):
    valid, count, error = _probe(data.library_path.strip())
    return CalibreTestResponse(success=valid, book_count=count, error=error)


# ---------------------------------------------------------------------------
# Books (any authenticated user)
# ---------------------------------------------------------------------------
@router.get("/books")
async def list_books(
    search: Optional[str] = None,
    sort: str = Query("added", pattern="^(title|author|added|pubdate)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    library_path = _require_library(db)
    try:
        books, total = calibre_service.list_books(
            library_path, search=search, sort=sort, page=page, page_size=page_size
        )
    except calibre_service.CalibreError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    overlay_enabled, prefer_local = _overlay_config(db)
    if overlay_enabled and books:
        links = calibre_link_service.get_links_for_calibre_ids(
            db, [b["id"] for b in books]
        )
        books = [
            calibre_link_service.overlay_book_dict(
                b, links.get(b["id"]), prefer_local=prefer_local
            )
            for b in books
        ]
    return {"books": books, "total": total, "page": page, "page_size": page_size}


@router.get("/books/{book_id}")
async def get_book(
    book_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
) -> dict[str, Any]:
    library_path = _require_library(db)
    try:
        book = calibre_service.get_book(library_path, book_id)
    except calibre_service.CalibreError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    overlay_enabled, prefer_local = _overlay_config(db)
    if overlay_enabled:
        link = calibre_link_service.get_links_for_calibre_ids(db, [book_id]).get(book_id)
        book = calibre_link_service.overlay_book_dict(
            book, link, prefer_local=prefer_local
        )
    return book


@router.get("/books/{book_id}/cover")
async def get_cover(
    book_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    library_path = _require_library(db)
    path = calibre_service.cover_file(library_path, book_id)
    if not path:
        raise HTTPException(status_code=404, detail="Cover not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/books/{book_id}/download")
async def download_format(
    book_id: int,
    format: str = Query(..., min_length=1, max_length=10),
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    library_path = _require_library(db)
    result = calibre_service.format_file(library_path, book_id, format)
    if result is None:
        raise HTTPException(status_code=404, detail="Format not available for this book")
    path, download_name, media_type = result
    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name,
        content_disposition_type="attachment",
    )


class EmailBookRequest(BaseModel):
    format: str


class EmailBookResponse(BaseModel):
    success: bool
    message: str
    recipient: str


@router.post("/books/{book_id}/email", response_model=EmailBookResponse)
async def email_book_to_self(
    book_id: int,
    body: EmailBookRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Email a book file from the Calibre library to the current user's delivery address."""
    from app.services.email_service import send_book_email, EmailError

    if not (current_user.book_delivery_email or "").strip():
        raise HTTPException(
            status_code=400,
            detail="No delivery email address is set. Add one under Settings.",
        )

    library_path = _require_library(db)
    result = calibre_service.format_file(library_path, book_id, body.format)
    if result is None:
        raise HTTPException(status_code=404, detail="Format not available for this book")
    path, download_name, media_type = result

    book = calibre_service.get_book(library_path, book_id)
    book_title = (book or {}).get("title") or download_name

    try:
        send_book_email(
            db,
            current_user,
            file_path=path,
            download_name=download_name,
            media_type=media_type,
            book_title=book_title,
            book_format=body.format.upper(),
        )
    except EmailError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return EmailBookResponse(
        success=True,
        message=f"Sent to {current_user.book_delivery_email}",
        recipient=current_user.book_delivery_email,
    )


# ---------------------------------------------------------------------------
# Metadata overlay: link a Calibre book to a bookkeep Book / Hardcover record
# ---------------------------------------------------------------------------
class OverlaySettings(BaseModel):
    enabled: bool
    prefer_local: bool


class LinkRequest(BaseModel):
    book_id: Optional[int] = None
    hardcover_id: Optional[int] = None


class LinkResponse(BaseModel):
    linked_book_id: Optional[int]
    link_source: Optional[str]
    link_confirmed: bool
    hardcover_id: Optional[int]


@router.get("/overlay-settings", response_model=OverlaySettings)
async def get_overlay_settings(
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    enabled, prefer_local = _overlay_config(db)
    return OverlaySettings(enabled=enabled, prefer_local=prefer_local)


@router.put("/overlay-settings", response_model=OverlaySettings)
async def update_overlay_settings(
    data: OverlaySettings,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    for key, value in (
        (OVERLAY_ENABLED_KEY, data.enabled),
        (OVERLAY_PREFER_LOCAL_KEY, data.prefer_local),
    ):
        row = db.query(models.AppSettings).filter(models.AppSettings.key == key).first()
        if row is None:
            row = models.AppSettings(key=key, value="true" if value else "false")
            db.add(row)
        else:
            row.value = "true" if value else "false"
    db.commit()
    logger.info("calibre_overlay_settings_updated", enabled=data.enabled, prefer_local=data.prefer_local)
    return data


async def _resolve_book_for_link(
    data: LinkRequest, db: Session
) -> models.Book:
    if data.book_id:
        book = db.query(models.Book).filter(models.Book.id == data.book_id).first()
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return book
    if data.hardcover_id:
        book = (
            db.query(models.Book)
            .filter(models.Book.hardcover_id == data.hardcover_id)
            .first()
        )
        if book is not None:
            return book
        from app.routers.settings import get_hardcover_token
        from app.tasks import _ensure_book_in_db

        token, _src = get_hardcover_token(db)
        if not token:
            raise HTTPException(status_code=400, detail="Hardcover API token is not configured")
        book = await _ensure_book_in_db(data.hardcover_id, token, db)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found on Hardcover")
        return book
    raise HTTPException(status_code=400, detail="Provide either book_id or hardcover_id")


@router.put("/books/{book_id}/link", response_model=LinkResponse)
async def set_book_link(
    book_id: int,
    data: LinkRequest,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """Manually link this Calibre book to a bookkeep Book / Hardcover record."""
    library_path = _require_library(db)
    cal = calibre_service.get_book(library_path, book_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="Calibre book not found")

    book = await _resolve_book_for_link(data, db)
    isbn = (cal.get("identifiers") or {}).get("isbn")
    calibre_link_service.upsert_link(
        db,
        calibre_book_id=book_id,
        book_id=book.id,
        source="manual",
        confidence=None,
        confirmed=True,
        calibre_isbn=isbn,
        calibre_title=cal.get("title"),
    )

    # Reflect the library's formats onto the Book so the rest of the app treats
    # it as owned.
    kinds = {
        calibre_service.classify_format(d["format"])
        for d in (cal.get("format_details") or [])
    }
    if "ebook" in kinds:
        book.ebook_available = True
    if "audiobook" in kinds:
        book.audiobook_available = True
    db.commit()

    from app.services.book_metadata import enrich_book

    try:
        await enrich_book(db, book, overwrite=True, resolve_hardcover=True, use_google=True)
        if book.last_refreshed is None:
            from datetime import datetime, timezone

            book.last_refreshed = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # linking still succeeded
        db.rollback()
        logger.warning("calibre_manual_link_enrich_failed", book_id=book.id, error=str(exc))

    return LinkResponse(
        linked_book_id=book.id,
        link_source="manual",
        link_confirmed=True,
        hardcover_id=book.hardcover_id,
    )


class CalibreByHardcoverResponse(BaseModel):
    calibre_book_id: int
    title: Optional[str]
    link_source: Optional[str]
    link_confirmed: bool
    ebook_formats: list[str]
    audiobook_formats: list[str]
    format_details: list[dict[str, Any]]


@router.get("/by-hardcover/{hardcover_id}", response_model=CalibreByHardcoverResponse)
async def calibre_book_by_hardcover(
    hardcover_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    """Resolve a Hardcover book id to its linked Calibre library book, if any.

    Used by the shared book detail page to offer download / email straight from
    the library. 404 when there is no link or no readable library.
    """
    link = (
        db.query(models.CalibreBookLink)
        .join(models.Book, models.Book.id == models.CalibreBookLink.book_id)
        .filter(models.Book.hardcover_id == hardcover_id)
        .first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail="No linked Calibre book")

    library_path = get_active_library_path(db)
    if not library_path:
        raise HTTPException(status_code=404, detail="Calibre library is not configured")

    cal = calibre_service.get_book(library_path, link.calibre_book_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="Linked Calibre book not found")

    details = cal.get("format_details") or []
    ebook = [d["format"] for d in details if calibre_service.classify_format(d["format"]) == "ebook"]
    audio = [d["format"] for d in details if calibre_service.classify_format(d["format"]) == "audiobook"]
    return CalibreByHardcoverResponse(
        calibre_book_id=link.calibre_book_id,
        title=cal.get("title"),
        link_source=link.source,
        link_confirmed=bool(link.confirmed),
        ebook_formats=ebook,
        audiobook_formats=audio,
        format_details=details,
    )


@router.delete("/books/{book_id}/link")
async def clear_book_link(
    book_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    removed = calibre_link_service.delete_link_for_calibre_id(db, book_id)
    return {"removed": removed}


@router.post("/books/{book_id}/refresh-metadata", response_model=LinkResponse)
async def refresh_book_metadata(
    book_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    """Re-fetch metadata (Open Library + Hardcover) for the Book linked to this Calibre book."""
    link = calibre_link_service.get_links_for_calibre_ids(db, [book_id]).get(book_id)
    if link is None or link.book is None:
        raise HTTPException(status_code=404, detail="This book is not linked yet")

    from app.services.book_metadata import enrich_book
    from datetime import datetime, timezone

    book = link.book
    try:
        await enrich_book(db, book, overwrite=True, resolve_hardcover=True, use_google=True)
        book.last_refreshed = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Metadata refresh failed: {exc}")

    return LinkResponse(
        linked_book_id=book.id,
        link_source=link.source,
        link_confirmed=bool(link.confirmed),
        hardcover_id=book.hardcover_id,
    )
