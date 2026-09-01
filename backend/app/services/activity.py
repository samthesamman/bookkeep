"""User activity tracking for the admin usage stats.

``record_activity`` is called from the auth dependency on every authenticated
request, so it must stay cheap: an in-process throttle skips the DB entirely
unless the user has not been recorded in the last few minutes. When it does
write, it bumps ``users.last_seen_at`` and the per-day ``user_activity_days``
counter using its own short-lived session so it never interferes with the
request's transaction.
"""
from datetime import datetime, timezone, timedelta

import structlog

from app.database import SessionLocal
from app.models import User, UserActivityDay

logger = structlog.get_logger(__name__)

# Only touch the DB at most once per user per this window.
_ACTIVITY_THROTTLE = timedelta(minutes=5)

# user_id -> last time we wrote a row for them (this process only).
_last_recorded: dict[int, datetime] = {}


def record_activity(user_id: int) -> None:
    """Record that ``user_id`` was active now, throttled and best-effort."""
    now = datetime.now(timezone.utc)
    last = _last_recorded.get(user_id)
    if last is not None and now - last < _ACTIVITY_THROTTLE:
        return
    _last_recorded[user_id] = now

    db = SessionLocal()
    try:
        db.query(User).filter(User.id == user_id).update(
            {User.last_seen_at: now}, synchronize_session=False
        )
        today = now.date()
        updated = (
            db.query(UserActivityDay)
            .filter(
                UserActivityDay.user_id == user_id,
                UserActivityDay.day == today,
            )
            .update(
                {UserActivityDay.request_count: UserActivityDay.request_count + 1},
                synchronize_session=False,
            )
        )
        if not updated:
            db.add(
                UserActivityDay(user_id=user_id, day=today, request_count=1)
            )
        db.commit()
    except Exception as e:
        db.rollback()
        # A racing insert on the same (user_id, day) is the common case; the
        # lost ping is not worth retrying for.
        logger.warning("record_activity_failed", user_id=user_id, error=str(e))
    finally:
        db.close()


def get_activity_stats(db, days: int = 30) -> dict[int, dict]:
    """Return ``{user_id: {"active_days": int, "events": int}}`` over the window."""
    from sqlalchemy import func

    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = (
        db.query(
            UserActivityDay.user_id,
            func.count(UserActivityDay.day),
            func.coalesce(func.sum(UserActivityDay.request_count), 0),
        )
        .filter(UserActivityDay.day >= since)
        .group_by(UserActivityDay.user_id)
        .all()
    )
    return {
        user_id: {"active_days": int(active_days), "events": int(events)}
        for user_id, active_days, events in rows
    }
