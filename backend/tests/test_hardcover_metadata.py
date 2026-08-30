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
    "contributions": [
        {"author": {"name": "Frank Herbert"}},
        {"author": {"name": "Frank Herbert"}},  # dedup
    ],
    "editions": [
        {"isbn_13": "9789113094281", "language": {"code3": "swe"}, "publisher": {"name": "Nova"}},
        {"isbn_13": "9780441013593", "language": {"code3": "eng"}, "publisher": {"name": "Ace Books"}},
    ],
}


def test_normalize_maps_fields():
    out = normalize(_PAYLOAD)
    assert out["description"] == "A desert epic."
    assert out["author"] == "Frank Herbert"
    # English edition preferred for publisher / ISBN
    assert out["publisher"] == "Ace Books"
    assert out["isbn"] == "9780441013593"
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
    assert out["author"] is None and out["publisher"] is None and out["isbn"] is None


def test_normalize_empty_is_empty_dict():
    assert normalize(None) == {}
    assert normalize({}) == {}
