"""Tests for app.services.openlibrary_metadata."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import openlibrary_metadata as ol


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_title_match():
    assert ol._title_match("The Final Empire", "Mistborn: The Final Empire")
    assert ol._title_match("Dune", "Dune")
    assert not ol._title_match("Dune", "Dune Messiah")
    assert not ol._title_match("The Hobbit", "A Wizard of Earthsea")


def test_clean_subjects_filters_noise_and_caps():
    raw = [
        "Fantasy fiction",
        "fantasy fiction",  # dupe (case)
        "Accessible book",  # noise
        "Internet Archive Wishlist",  # noise
        "science fiction",
        "A very long subject string that goes well past the length limit we allow",
        "Magic",
        "Dragons",
        "Heroes",
        "Quests",
        "Adventure",
        "Coming of age",
    ]
    out = ol._clean_subjects(raw)
    assert "Fantasy Fiction" in out
    assert "Science Fiction" in out
    assert all("wishlist" not in s.lower() for s in out)
    assert len(out) <= ol._MAX_GENRES


def test_description_text_unwraps_and_trims_source_note():
    assert ol._description_text({"description": "  Plain text.  "}) == "Plain text."
    assert ol._description_text({"description": {"value": "Rich text."}}) == "Rich text."
    assert (
        ol._description_text({"description": "Real blurb.\n----------\nSource: somewhere"})
        == "Real blurb."
    )
    assert ol._description_text({}) is None


# --------------------------------------------------------------------------- #
# fetch()
# --------------------------------------------------------------------------- #
def _resp(json_body):
    r = MagicMock()
    r.json.return_value = json_body
    r.raise_for_status.return_value = None
    return r


def _client_returning(*responses):
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx), client


@pytest.mark.asyncio
async def test_fetch_by_isbn_happy_path():
    search = _resp(
        {
            "docs": [
                {
                    "key": "/works/OL1W",
                    "title": "Dune",
                    "first_publish_year": 1965,
                    "cover_i": 99,
                    "number_of_pages_median": 604,
                }
            ]
        }
    )
    work = _resp({"description": {"value": "A desert epic."}, "subjects": ["Science Fiction"]})
    factory, client = _client_returning(search, work)

    with patch.object(ol.httpx, "AsyncClient", factory):
        out = await ol.fetch(isbn="978-0-441-01359-3", title="Dune", author="Herbert")

    assert out["description"] == "A desert epic."
    assert out["cover_url"].endswith("99-L.jpg")
    assert out["page_count"] == 604
    assert out["published_date"] == "1965"
    assert out["genres"] == ["Science Fiction"]
    assert "rating" not in out  # Open Library never contributes ratings
    assert client.get.await_count == 2
    assert client.get.await_args_list[0].kwargs["params"]["isbn"] == "9780441013593"


@pytest.mark.asyncio
async def test_fetch_title_search_rejects_mismatch():
    search = _resp({"docs": [{"key": "/works/OL9W", "title": "Completely Different Book"}]})
    factory, client = _client_returning(search)

    with patch.object(ol.httpx, "AsyncClient", factory):
        out = await ol.fetch(title="Dune", author="Frank Herbert")

    assert out is None
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_docs():
    factory, _ = _client_returning(_resp({"docs": []}), _resp({"docs": []}))
    with patch.object(ol.httpx, "AsyncClient", factory):
        assert await ol.fetch(isbn="123", title="Nothing Here") is None


@pytest.mark.asyncio
async def test_fetch_needs_isbn_or_title():
    assert await ol.fetch() is None
