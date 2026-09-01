"""Tests for app.services.activity (admin usage tracking)."""
from datetime import datetime, timezone, timedelta, date

import pytest

from app.database import SessionLocal, engine, Base
from app.models import User, UserActivityDay
from app.services import activity


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.query(UserActivityDay).delete()
        session.query(User).delete()
        session.commit()
        session.close()
        activity._last_recorded.clear()


def _make_user(db, uid: int) -> User:
    user = User(
        id=uid, email=f"u{uid}@example.com", username=f"u{uid}", hashed_password="x"
    )
    db.add(user)
    db.commit()
    return user


def test_record_activity_sets_last_seen_and_day_row(db):
    _make_user(db, 1)

    activity.record_activity(1)

    db.expire_all()
    user = db.query(User).filter(User.id == 1).one()
    assert user.last_seen_at is not None
    row = db.query(UserActivityDay).filter_by(user_id=1).one()
    assert row.day == datetime.now(timezone.utc).date()
    assert row.request_count == 1


def test_record_activity_is_throttled_within_window(db):
    _make_user(db, 1)

    activity.record_activity(1)
    activity.record_activity(1)  # still inside the throttle window -> no-op

    row = db.query(UserActivityDay).filter_by(user_id=1).one()
    assert row.request_count == 1


def test_record_activity_increments_after_throttle_expires(db):
    _make_user(db, 1)

    activity.record_activity(1)
    activity._last_recorded[1] = datetime.now(timezone.utc) - timedelta(minutes=10)
    activity.record_activity(1)

    row = db.query(UserActivityDay).filter_by(user_id=1).one()
    assert row.request_count == 2


def test_get_activity_stats_windows_to_last_30_days(db):
    _make_user(db, 1)
    today = date.today()
    db.add(UserActivityDay(user_id=1, day=today, request_count=3))
    db.add(UserActivityDay(user_id=1, day=today - timedelta(days=5), request_count=2))
    db.add(UserActivityDay(user_id=1, day=today - timedelta(days=45), request_count=9))
    db.commit()

    stats = activity.get_activity_stats(db, days=30)

    assert stats[1]["active_days"] == 2
    assert stats[1]["events"] == 5


def test_get_activity_stats_empty_for_unknown_user(db):
    _make_user(db, 1)
    assert activity.get_activity_stats(db) == {}
