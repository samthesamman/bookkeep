"""Tests for app.services.book_metadata.

The Hardcover fetch path pulls in app.routers.hardcover, which cannot be
imported in this environment (no python-jose), so enrich_book tests mock
_hardcover_payload / the source fetchers directly.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Book
from app.services import book_metadata as bm


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


def test_merge_cover_prefers_hardcover():
    b = Book(title="Dune", author="Frank Herbert")
    bm._merge(b, _sources(gb={"cover_url": "gb.jpg"}, hc={"cover_url": "hc.jpg"}), overwrite=False)
    assert b.cover_url == "hc.jpg"


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


# --------------------------------------------------------------------------- #
# enrich_book
# --------------------------------------------------------------------------- #
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
