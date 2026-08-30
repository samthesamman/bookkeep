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


def test_cover_url_none_for_bare_catalog_thumbnail():
    # Only thumbnail/smallThumbnail from /books/content = the only image Google
    # has; upsizing it renders a placeholder, so don't use it at all.
    assert (
        gb._cover_url(
            {
                "smallThumbnail": "http://books.google.com/books/content?id=X&img=1&zoom=5",
                "thumbnail": "http://books.google.com/books/content?id=X&printsec=frontcover&img=1&zoom=1&edge=curl",
            }
        )
        is None
    )


def test_cover_url_uses_publisher_art_and_strips_params():
    out = gb._cover_url(
        {
            "smallThumbnail": "http://books.google.com/books/publisher/content?id=X&img=1&zoom=5&edge=curl",
            "thumbnail": "http://books.google.com/books/publisher/content?id=X&printsec=frontcover&img=1&zoom=1&edge=curl&imgtk=AFLRE7abc&source=gbs_api",
        }
    )
    assert out == "https://books.google.com/books/publisher/content?id=X&printsec=frontcover&img=1&source=gbs_api&fife=w800"
    assert "zoom=" not in out and "edge=curl" not in out and "imgtk" not in out


def test_cover_url_uses_multi_size_content_links():
    out = gb._cover_url(
        {
            "thumbnail": "http://books.google.com/books/content?id=Y&printsec=frontcover&img=1&zoom=1",
            "large": "http://books.google.com/books/content?id=Y&printsec=frontcover&img=1&zoom=3",
        }
    )
    assert out == "https://books.google.com/books/content?id=Y&printsec=frontcover&img=1&fife=w800"


def test_cover_url_skips_interior_page_scans():
    assert gb._cover_url({"large": "http://x/books/content?id=Z&pg=PA1&img=1&zoom=3"}) is None


def test_categories_splits_and_drops_generic():
    cats = ["Fiction / Fantasy / Epic", "Fiction / Action & Adventure", "General"]
    out = gb._categories(cats)
    assert "Fantasy" in out and "Epic" in out
    assert "Fiction" not in out and "General" not in out


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
                    "authors": ["Frank Herbert"],
                    "publisher": "Ace Books",
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "0441013597"},
                        {"type": "ISBN_13", "identifier": "9780441013593"},
                    ],
                    "description": "<p>A desert epic.</p>",
                    "pageCount": 604,
                    "publishedDate": "1965-08-01",
                    "averageRating": 4.5,
                    "ratingsCount": 1200,
                    "categories": ["Fiction / Science Fiction"],
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/publisher/content?id=x&printsec=frontcover&img=1&zoom=1&edge=curl"
                    },
                }
            }
        ]
    }
    factory, client = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(isbn="978-0-441-01359-3", title="Dune", author="Herbert")

    assert out["description"] == "A desert epic."
    assert out["author"] == "Frank Herbert"
    assert out["publisher"] == "Ace Books"
    assert out["isbn"] == "9780441013593"  # ISBN_13 preferred
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
async def test_fetch_title_search_matches_by_subtitle_and_falls_back_to_main():
    wanted = "No Bad Parts: Healing Trauma and Restoring Wholeness"
    # First query (full title): only a wrong-but-word-overlapping hit -> rejected.
    first = _resp({"items": [{"volumeInfo": {"title": "Healing Trauma"}}]})
    # Second query (main title only): the real book, with title/subtitle split.
    second = _resp(
        {
            "items": [
                {
                    "volumeInfo": {
                        "title": "No Bad Parts",
                        "subtitle": "Healing Trauma and Restoring Wholeness",
                        "description": "IFS.",
                    }
                }
            ]
        }
    )
    factory, client = _client_returning(first, second)
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(title=wanted, author="Richard Schwartz")

    assert out is not None and out["title"] == "No Bad Parts"
    assert client.get.await_count == 2
    assert "No Bad Parts" in client.get.await_args_list[1].kwargs["params"]["q"]
    assert "Healing Trauma" not in client.get.await_args_list[1].kwargs["params"]["q"]


@pytest.mark.asyncio
async def test_fetch_needs_isbn_or_title():
    assert await gb.fetch() is None


@pytest.mark.asyncio
async def test_fetch_hydrates_truncated_search_result(monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    # Search gives a truncated volumeInfo (no description); the /volumes/{id}
    # GET returns the full record.
    search = _resp(
        {"items": [{"id": "VOL1", "volumeInfo": {"title": "Dune", "publisher": "Ace", "pageCount": 604}}]}
    )
    hydrated = _resp(
        {
            "volumeInfo": {
                "title": "Dune",
                "description": "A desert epic.",
                "pageCount": 604,
                "imageLinks": {
                    "thumbnail": "http://books.google.com/books/publisher/content?id=VOL1&printsec=frontcover&img=1&zoom=1"
                },
            }
        }
    )
    factory, client = _client_returning(search, hydrated)
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(title="Dune", author="Frank Herbert")

    assert out["description"] == "A desert epic."
    assert out["cover_url"] and "frontcover" in out["cover_url"]
    assert client.get.await_count == 2
    assert client.get.await_args_list[1].args[0].endswith("/volumes/VOL1")


@pytest.mark.asyncio
async def test_fetch_sends_country_and_picks_richest_match(monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_BOOKS_COUNTRY", raising=False)
    body = {
        "items": [
            {"volumeInfo": {"title": "Dune"}},  # bare — no description/cover
            {
                "volumeInfo": {
                    "title": "Dune",
                    "description": "<p>A desert epic.</p>",
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/publisher/content?id=x&printsec=frontcover&img=1&zoom=1"
                    },
                }
            },
        ]
    }
    factory, client = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(title="Dune", author="Frank Herbert")

    assert out is not None and out["description"] == "A desert epic."
    assert out["cover_url"] and "frontcover" in out["cover_url"]
    assert client.get.await_args_list[0].kwargs["params"]["country"] == "US"


@pytest.mark.asyncio
async def test_fetch_prefers_english_edition():
    body = {
        "items": [
            {
                "volumeInfo": {
                    "title": "Dune",
                    "language": "de",
                    "description": "Der Wüstenplanet.",
                    "publisher": "Heyne",
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/publisher/content?id=d&printsec=frontcover&img=1&zoom=1"
                    },
                }
            },
            {
                "volumeInfo": {
                    "title": "Dune",
                    "language": "en",
                    "description": "A desert epic.",
                }
            },
        ]
    }
    factory, _ = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(title="Dune", author="Frank Herbert")

    assert out["description"] == "A desert epic."  # English edition, even though poorer


@pytest.mark.asyncio
async def test_fetch_no_english_match_keeps_only_language_neutral_fields():
    body = {
        "items": [
            {
                "volumeInfo": {
                    "title": "Dune",
                    "language": "de",
                    "description": "Der Wüstenplanet.",  # German — dropped
                    "categories": ["Belletristik"],  # German — dropped
                    "pageCount": 800,  # edition-specific — dropped (no English edition)
                    "publisher": "Heyne",  # language-neutral — kept
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/publisher/content?id=d&printsec=frontcover&img=1&zoom=1"
                    },
                }
            }
        ]
    }
    factory, _ = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(title="Dune", author="Frank Herbert")

    assert out is not None
    assert out["description"] is None
    assert out["genres"] == []
    assert out["page_count"] is None
    assert out["publisher"] == "Heyne"
    assert out["cover_url"] and "frontcover" in out["cover_url"]


@pytest.mark.asyncio
async def test_fetch_stub_english_edition_borrows_cover_from_foreign_edition():
    # Google's English entry is a catalog stub (no desc, 0 pages, placeholder
    # cover); the only real data is a German edition.
    body = {
        "items": [
            {
                "volumeInfo": {
                    "title": "The Poison Daughter",
                    "language": "en",
                    "pageCount": 0,
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9781960416278"}],
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/content?id=STUB&printsec=frontcover&img=1&zoom=1"
                    },
                }
            },
            {
                "volumeInfo": {
                    "title": "The Poison Daughter",
                    "subtitle": "Zum Sterben schön",
                    "language": "de",
                    "description": "Deutsche Beschreibung.",
                    "pageCount": 1146,
                    "publisher": "Random House GmbH",
                    "imageLinks": {
                        "thumbnail": "http://books.google.com/books/publisher/content?id=REAL&printsec=frontcover&img=1&zoom=1&edge=curl"
                    },
                }
            },
        ]
    }
    factory, _ = _client_returning(_resp(body))
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(title="The Poison Daughter", author="Sheila Masterson")

    assert out["description"] is None            # no English blurb
    assert out["page_count"] is None             # English edition has none
    assert out["cover_url"] == "https://books.google.com/books/publisher/content?id=REAL&printsec=frontcover&img=1&fife=w800"
    assert out["isbn"] == "9781960416278"        # English edition's ISBN


@pytest.mark.asyncio
async def test_fetch_isbn_bare_volume_falls_through_to_title_search():
    isbn_resp = _resp({"items": [{"volumeInfo": {"title": "Dune"}}]})  # no desc/cover
    title_resp = _resp(
        {"items": [{"volumeInfo": {"title": "Dune", "description": "<p>epic</p>"}}]}
    )
    factory, client = _client_returning(isbn_resp, title_resp)
    with patch.object(gb.httpx, "AsyncClient", factory):
        out = await gb.fetch(isbn="9780441013593", title="Dune")

    assert out["description"] == "epic"
    assert client.get.await_count == 2


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
