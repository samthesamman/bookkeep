"""Tests for app.services.googlebooks_metadata."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import googlebooks_metadata as gb


def test_clean_html_flattens_to_plain_text():
    raw = "<p>First para.</p><p>Second <i>para</i> with a<br>line break.</p>&amp; done"
    out = gb._clean_html(raw)
    assert "<" not in out
    assert "First para." in out
    assert "line break." in out
    assert "& done" in out
    assert "\n\n" in out  # paragraph boundary preserved


def test_cover_url_prefers_largest_and_https():
    links = {
        "smallThumbnail": "http://x/s?edge=curl",
        "thumbnail": "http://books.google.com/t?zoom=1&edge=curl&x=1",
        "large": "http://books.google.com/l?zoom=2",
    }
    assert gb._cover_url(links) == "http://books.google.com/l?zoom=2".replace("http://", "https://")


def test_categories_splits_and_drops_generic():
    cats = ["Fiction / Fantasy / Epic", "Fiction / Action & Adventure", "General"]
    out = gb._categories(cats)
    assert "Fantasy" in out and "Epic" in out
    assert "Fiction" not in out and "General" not in out


def test_title_match():
    assert gb._title_match("Dune", "Dune")
    assert not gb._title_match("Dune", "Dune Messiah")


def _resp(json_body, status_code=200):
    r = MagicMock()
    r.status_code = status_code
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
async def test_fetch_by_isbn_happy_path(monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    body = {
        "items": [
            {
                "volumeInfo": {
                    "title": "Dune",
                    "description": "<p>A desert epic.</p>",
                    "pageCount": 604,
                    "publishedDate": "1965-08-01",
                    "averageRating": 4.5,
                    "ratingsCount": 1200,
                    "categories": ["Fiction / Science Fiction"],
                    "imageLinks": {"thumbnail": "http://books.google.com/x?zoom=1&edge=curl"},
                }
            }
        ]
    }
    factory, client = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(isbn="978-0-441-01359-3", title="Dune", author="Herbert")

    assert out["description"] == "A desert epic."
    assert out["page_count"] == 604
    assert out["published_date"] == "1965-08-01"
    assert out["rating"] == 4.5
    assert out["ratings_count"] == 1200
    assert out["genres"] == ["Science Fiction"]
    assert out["cover_url"].startswith("https://")
    assert "edge=curl" not in out["cover_url"]
    assert client.get.await_args_list[0].kwargs["params"]["q"] == "isbn:9780441013593"


@pytest.mark.asyncio
async def test_fetch_title_search_rejects_mismatch():
    body = {"items": [{"volumeInfo": {"title": "A Totally Different Book"}}]}
    factory, _ = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        assert await gb.fetch(title="Dune", author="Frank Herbert") is None


@pytest.mark.asyncio
async def test_fetch_needs_isbn_or_title():
    assert await gb.fetch() is None


@pytest.mark.asyncio
async def test_fetch_raises_googlebooks_error_on_rate_limit():
    factory, _ = _client_returning(
        _resp({}, status_code=429),
        _resp({}, status_code=429),
        _resp({}, status_code=429),
    )
    with patch.object(gb.httpx, "AsyncClient", factory), patch.object(
        gb.asyncio, "sleep", AsyncMock()
    ):
        with pytest.raises(gb.GoogleBooksError):
            await gb.fetch(title="Dune")
