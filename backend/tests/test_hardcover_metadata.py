"""Tests for app.services.hardcover_metadata.apply_hardcover_metadata."""
from app.models import Book
from app.services.hardcover_metadata import apply_hardcover_metadata

_PAYLOAD = {
    "id": 42,
    "description": "A desert epic.",
    "pages": 604,
    "rating": 4.25,
    "ratings_count": 1000,
    "release_date": "1965-08-01",
    "cached_image": {"url": "http://img/dune.jpg"},
    "book_series": [{"position": 1.0, "series": {"id": 7, "name": "Dune"}}],
    "taggings": [{"tag": {"tag": "Sci-Fi"}}, {"tag": {"tag": "Classic"}}],
}


def test_fills_empty_fields():
    b = Book(title="Dune", author="Frank Herbert")
    assert apply_hardcover_metadata(b, _PAYLOAD) is True
    assert b.description == "A desert epic."
    assert b.page_count == 604
    assert b.rating == 4.25
    assert b.cover_url == "http://img/dune.jpg"
    assert b.series == "Dune" and b.series_id == 7 and b.series_position == 1.0
    assert b.genres == "Sci-Fi, Classic"
    assert b.published_date == "1965-08-01"
    assert b.last_refreshed is not None


def test_does_not_overwrite_by_default():
    b = Book(title="Dune", author="Frank Herbert", description="mine", rating=3.0)
    apply_hardcover_metadata(b, _PAYLOAD)
    assert b.description == "mine"
    assert b.rating == 3.0
    assert b.page_count == 604  # still fills the empty one


def test_overwrite_true_replaces():
    b = Book(title="Dune", author="Frank Herbert", description="mine", rating=3.0)
    apply_hardcover_metadata(b, _PAYLOAD, overwrite=True)
    assert b.description == "A desert epic."
    assert b.rating == 4.25


def test_empty_payload_is_noop():
    b = Book(title="Dune", author="Frank Herbert")
    assert apply_hardcover_metadata(b, {}) is False
    assert b.last_refreshed is None
