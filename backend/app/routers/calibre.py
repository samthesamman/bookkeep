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
from app.services import calibre_service

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
