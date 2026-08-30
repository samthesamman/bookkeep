"""Tests for app.services.applebooks_metadata."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import applebooks_metadata as ab


def test_artwork_hi_res_swaps_size_segment():
    url = (
        "https://is1-ssl.mzstatic.com/image/thumb/Publication221/v4/a3/92/5e/"
        "a3925e01-8b7d/9783641351670.jpg/100x100bb.jpg"
    )
    assert ab._artwork_hi_res(url).endswith("/9783641351670.jpg/1400x1400bb.jpg")
    assert ab._artwork_hi_res(None) is None


def test_genres_drops_noise():
    out = ab._genres(["Romance", "Books", "Fiction", "Sci-Fi & Fantasy", "Fantasy"])
    assert out == ["Romance", "Sci-Fi & Fantasy", "Fantasy"]


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


_ITEM = {
    "trackName": "The Poison Daughter",
    "artistName": "Sheila Masterson",
    "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Pub/v4/x/9783641351670.jpg/100x100bb.jpg",
    "description": "<p>A dark romantasy.</p>",
    "releaseDate": "2025-10-03T07:00:00Z",
    "averageUserRating": 4.5,
    "userRatingCount": 128,
    "genres": ["Romance", "Books", "Fantasy"],
}


@pytest.mark.asyncio
async def test_fetch_by_title_matches_and_normalizes():
    factory, client = _client_returning(_resp({"results": [_ITEM]}))
    with patch.object(ab.httpx, "AsyncClient", factory):
        out = await ab.fetch(title="The Poison Daughter", author="Sheila Masterson")

    assert out["source"] == "applebooks"
    assert out["cover_url"].endswith("/1400x1400bb.jpg")
    assert out["description"] == "A dark romantasy."
    assert out["published_date"] == "2025-10-03"
    assert out["rating"] == 4.5 and out["ratings_count"] == 128
    assert out["genres"] == ["Romance", "Fantasy"]
    assert client.get.await_args_list[0].kwargs["params"]["entity"] == "ebook"


@pytest.mark.asyncio
async def test_fetch_rejects_title_mismatch():
    bad = {**_ITEM, "trackName": "A Completely Different Book"}
    factory, _ = _client_returning(_resp({"results": [bad]}))
    with patch.object(ab.httpx, "AsyncClient", factory):
        assert await ab.fetch(title="The Poison Daughter") is None


@pytest.mark.asyncio
async def test_fetch_skips_results_without_artwork():
    no_art = {k: v for k, v in _ITEM.items() if k != "artworkUrl100"}
    factory, _ = _client_returning(_resp({"results": [no_art]}))
    with patch.object(ab.httpx, "AsyncClient", factory):
        assert await ab.fetch(title="The Poison Daughter") is None


@pytest.mark.asyncio
async def test_fetch_raises_on_rate_limit():
    factory, _ = _client_returning(*[_resp({}, status_code=429)] * 3)
    with patch.object(ab.httpx, "AsyncClient", factory), patch.object(
        ab.asyncio, "sleep", AsyncMock()
    ):
        with pytest.raises(ab.AppleBooksError):
            await ab.fetch(title="Dune")


@pytest.mark.asyncio
async def test_fetch_needs_isbn_or_title():
    assert await ab.fetch() is None
