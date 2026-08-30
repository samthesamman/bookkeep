"""Tests for app.services.book_metadata.

The Hardcover fetch path pulls in app.routers.hardcover, which cannot be
imported in this environment (no python-jose), so enrich_book tests mock
_hardcover_payload / the source fetchers directly.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Book
from app.services import book_metadata as bm


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # enrich_book calls Apple + Google Books when use_google=True; stub Apple by
    # default so tests that only mock gb/ol/hc don't hit the network.
    monkeypatch.setattr(bm.ab, "fetch", AsyncMock(return_value=None))


# --------------------------------------------------------------------------- #
# _merge — per-field source priority
# --------------------------------------------------------------------------- #
def _sources(**kw):
    return kw


def test_merge_description_prefers_google_then_hardcover_then_openlibrary():
    b = Book(title="Dune", author="Frank Herbert")
    bm._merge(
        b,
        _sources(
            gb={"description": "GB blurb"},
            hc={"description": "HC blurb"},
            ol={"description": "OL blurb"},
        ),
        overwrite=False,
    )
    assert b.description == "GB blurb"

    b2 = Book(title="Dune", author="Frank Herbert")
    bm._merge(b2, _sources(hc={"description": "HC blurb"}, ol={"description": "OL blurb"}), overwrite=False)
    assert b2.description == "HC blurb"


def test_merge_cover_prefers_apple_then_hardcover():
    b = Book(title="Dune", author="Frank Herbert")
    bm._merge(
        b,
        _sources(ab={"cover_url": "apple.jpg"}, hc={"cover_url": "hc.jpg"}, gb={"cover_url": "gb.jpg"}),
        overwrite=False,
    )
    assert b.cover_url == "apple.jpg"

    b2 = Book(title="Dune", author="Frank Herbert")
    bm._merge(b2, _sources(gb={"cover_url": "gb.jpg"}, hc={"cover_url": "hc.jpg"}), overwrite=False)
    assert b2.cover_url == "hc.jpg"  # no Apple -> Hardcover next


def test_merge_rating_and_count_taken_as_pair_from_hardcover():
    b = Book(title="Dune", author="Frank Herbert")
    bm._merge(
        b,
        _sources(
            hc={"rating": 4.3, "ratings_count": 5000},
            gb={"rating": 4.9, "ratings_count": 12},
        ),
        overwrite=False,
    )
    assert b.rating == 4.3 and b.ratings_count == 5000


def test_merge_series_only_from_hardcover():
    b = Book(title="Dune", author="Frank Herbert")
    bm._merge(
        b,
        _sources(hc={"series": "Dune", "series_id": 7, "series_position": 1.0}),
        overwrite=False,
    )
    assert b.series == "Dune" and b.series_id == 7 and b.series_position == 1.0


def test_merge_genres_priority_and_dedup():
    b = Book(title="Dune", author="Frank Herbert")
    bm._merge(
        b,
        _sources(hc={"genres": ["Sci-Fi", "Sci-Fi", "Classic"]}, gb={"genres": ["Fantasy"]}),
        overwrite=False,
    )
    assert b.genres == "Sci-Fi, Classic"


def test_merge_no_overwrite_keeps_existing():
    b = Book(title="Dune", author="Frank Herbert", description="mine")
    changed = bm._merge(b, _sources(gb={"description": "GB blurb"}), overwrite=False)
    assert b.description == "mine"
    assert changed is False


def test_merge_overwrite_replaces():
    b = Book(title="Dune", author="Frank Herbert", description="mine")
    bm._merge(b, _sources(gb={"description": "GB blurb"}), overwrite=True)
    assert b.description == "GB blurb"


def test_merge_overwrite_keeps_existing_when_source_field_is_empty():
    # Picking a source that has no cover must not wipe the current one.
    b = Book(title="Dune", author="Frank Herbert", cover_url="existing.jpg")
    bm._merge(b, _sources(ol={"description": "d", "cover_url": None}), overwrite=True)
    assert b.cover_url == "existing.jpg"


def test_apply_source_keeps_existing_cover_when_chosen_source_has_none():
    b = Book(title="Dune", author="Frank Herbert", cover_url="existing.jpg")
    bm.apply_source(b, "openlibrary", {"description": "d", "cover_url": None})
    assert b.cover_url == "existing.jpg"
    assert b.description == "d"


def test_apply_source_isbn_is_fill_only():
    b = Book(title="Dune", author="Frank Herbert")
    bm.apply_source(b, "googlebooks", {"isbn": "9780441013593"})
    assert b.isbn == "9780441013593"

    b2 = Book(title="Dune", author="Frank Herbert", isbn="1111111111111")
    bm.apply_source(b2, "googlebooks", {"isbn": "9780441013593"})
    assert b2.isbn == "1111111111111"  # not replaced — editions differ


def test_backfill_isbn_prefers_apple_and_fills_only():
    b = Book(title="Dune", author="Frank Herbert")
    changed = bm._backfill_isbn(None, b, _sources(ol={"isbn": "OL"}, ab={"isbn": "AB"}))
    assert changed and b.isbn == "AB"

    b2 = Book(title="Dune", author="Frank Herbert", isbn="existing")
    assert bm._backfill_isbn(None, b2, _sources(ab={"isbn": "AB"})) is False
    assert b2.isbn == "existing"


# --------------------------------------------------------------------------- #
# enrich_book
# --------------------------------------------------------------------------- #
def test_apply_source_writes_one_source_and_respects_fields():
    b = Book(title="Dune", author="Frank Herbert")
    gb_data = {"description": "GB blurb", "cover_url": "gb.jpg", "genres": ["Sci-Fi"], "rating": 4.9}

    assert bm.apply_source(b, "googlebooks", gb_data, fields=["description"]) is True
    assert b.description == "GB blurb"
    assert b.cover_url is None  # not in fields
    assert b.rating is None

    b2 = Book(title="Dune", author="Frank Herbert", description="old")
    bm.apply_source(b2, "googlebooks", gb_data)  # all fields, overwrite
    assert b2.description == "GB blurb"
    assert b2.cover_url == "gb.jpg"
    assert b2.genres == "Sci-Fi"


def test_apply_source_title_and_author_follow_fields():
    data = {"title": "Correct Title", "author": "Real Author", "description": "blurb"}

    b = Book(title="Wrong Title", author="Wrong Author")
    bm.apply_source(b, "googlebooks", data)  # default fields = all of APPLYABLE_FIELDS
    assert b.title == "Correct Title" and b.author == "Real Author"
    assert "title" in bm.APPLYABLE_FIELDS and "author" in bm.APPLYABLE_FIELDS

    b2 = Book(title="Wrong Title", author="Wrong Author")
    bm.apply_source(b2, "googlebooks", data, fields=["description"])
    assert b2.title == "Wrong Title" and b2.author == "Wrong Author"


@pytest.mark.asyncio
async def test_fetch_source_title_override_ignores_isbn_and_stored_id():
    book = Book(title="Old Wrong Title", author="Frank Herbert", isbn="9780441013593")
    book.hardcover_slug = "old-wrong"
    captured = {}

    async def fake_gb_fetch(*, isbn, title, author):
        captured.update(isbn=isbn, title=title, author=author)
        return {"title": title, "description": "d"}

    with patch.object(bm.gb, "fetch", fake_gb_fetch):
        await bm.fetch_source(None, book, "googlebooks", title="  Dune  ")

    assert captured == {"isbn": None, "title": "Dune", "author": "Frank Herbert"}


def test_apply_source_hardcover_adopts_id():
    b = Book(title="Dune", author="Frank Herbert")
    hc_data = {"description": "HC blurb", "hardcover_id": 42, "hardcover_slug": "dune", "series": "Dune"}
    assert bm.apply_source(b, "hardcover", hc_data) is True
    assert b.hardcover_id == 42 and b.hardcover_slug == "dune"
    assert b.series == "Dune"


def test_apply_source_unknown_or_empty_is_noop():
    b = Book(title="Dune", author="Frank Herbert")
    assert bm.apply_source(b, "bogus", {"description": "x"}) is False
    assert bm.apply_source(b, "googlebooks", {}) is False


@pytest.mark.asyncio
async def test_enrich_uses_google_only_when_asked():
    book = Book(title="Dune", author="Frank Herbert")
    gb_data = {"description": "GB blurb", "cover_url": "c.jpg", "genres": ["Sci-Fi"]}
    with patch.object(bm.gb, "fetch", AsyncMock(return_value=gb_data)) as gbfetch, patch.object(
        bm.ol, "fetch", AsyncMock(return_value=None)
    ), patch.object(bm, "_hardcover_payload", AsyncMock(return_value=None)):
        await bm.enrich_book(None, book, use_google=True)
    gbfetch.assert_awaited_once()
    assert book.description == "GB blurb"

    book2 = Book(title="Dune", author="Frank Herbert")
    with patch.object(bm.gb, "fetch", AsyncMock(return_value=gb_data)) as gbfetch2, patch.object(
        bm.ol, "fetch", AsyncMock(return_value={})
    ), patch.object(bm, "_hardcover_payload", AsyncMock(return_value=None)):
        await bm.enrich_book(None, book2, use_google=False)
    gbfetch2.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_skips_openlibrary_when_google_is_complete():
    book = Book(title="Dune", author="Frank Herbert")
    gb_data = {"description": "GB blurb", "cover_url": "c.jpg", "genres": ["Sci-Fi"]}
    with patch.object(bm.gb, "fetch", AsyncMock(return_value=gb_data)), patch.object(
        bm.ol, "fetch", AsyncMock(return_value={})
    ) as olfetch, patch.object(bm, "_hardcover_payload", AsyncMock(return_value=None)):
        await bm.enrich_book(None, book, use_google=True)
    olfetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_graceful_when_everything_misses():
    book = Book(title="Totally Unknown", author="Nobody")
    with patch.object(bm.gb, "fetch", AsyncMock(return_value=None)), patch.object(
        bm.ol, "fetch", AsyncMock(return_value=None)
    ), patch.object(bm, "_hardcover_payload", AsyncMock(return_value=None)):
        changed = await bm.enrich_book(None, book, use_google=True, resolve_hardcover=True)
    assert changed is False
    assert book.description is None
