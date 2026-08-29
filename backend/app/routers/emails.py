"""History of book files the current user has emailed to themselves."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import get_db

router = APIRouter()


class EmailLogResponse(BaseModel):
    id: int
    recipient: str
    subject: Optional[str] = None
    book_title: Optional[str] = None
    book_format: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/", response_model=List[EmailLogResponse])
async def list_my_emails(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return the current user's sent-email history, most recent first."""
    rows = (
        db.query(models.EmailLog)
        .filter(models.EmailLog.user_id == current_user.id)
        .order_by(models.EmailLog.created_at.desc(), models.EmailLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        EmailLogResponse(
            id=r.id,
            recipient=r.recipient,
            subject=r.subject,
            book_title=r.book_title,
            book_format=r.book_format,
            status=r.status,
            error_message=r.error_message,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]
