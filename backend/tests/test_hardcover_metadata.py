"""Tests for app.services.hardcover_metadata.normalize."""
from app.services.hardcover_metadata import normalize

_PAYLOAD = {
    "id": 42,
    "slug": "dune",
    "title": "Dune",
    "description": "A desert epic.",
    "pages": 604,
    "rating": 4.25,
    "ratings_count": 1000,
    "release_date": "1965-08-01",
    "cached_image": {"url": "http://img/dune.jpg"},
    "book_series": [{"position": 1.0, "series": {"id": 7, "name": "Dune"}}],
    "taggings": [{"tag": {"tag": "Sci-Fi"}}, {"tag": {"tag": "Classic"}}],
}


def test_normalize_maps_fields():
    out = normalize(_PAYLOAD)
    assert out["description"] == "A desert epic."
    assert out["cover_url"] == "http://img/dune.jpg"
    assert out["page_count"] == 604
    assert out["published_date"] == "1965-08-01"
    assert out["rating"] == 4.25
    assert out["ratings_count"] == 1000
    assert out["genres"] == ["Sci-Fi", "Classic"]
    assert out["series"] == "Dune"
    assert out["series_id"] == 7
    assert out["series_position"] == 1.0
    assert out["hardcover_id"] == 42
    assert out["hardcover_slug"] == "dune"


def test_normalize_published_date_falls_back_to_year():
    out = normalize({"id": 1, "release_year": 1965})
    assert out["published_date"] == "1965"


def test_normalize_without_series_omits_series_keys():
    out = normalize({"id": 1, "title": "Standalone"})
    assert "series" not in out
    assert out["genres"] == []


def test_normalize_empty_is_empty_dict():
    assert normalize(None) == {}
    assert normalize({}) == {}
