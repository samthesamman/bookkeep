"""Tests for the NYT Books API client (Best Sellers lists)."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import nyt_bestsellers as nyt


def _mock_async_client(get_mock: AsyncMock) -> MagicMock:
    """Build a patch target that mimics ``async with httpx.AsyncClient() as c``."""
    client = MagicMock()
    client.get = get_mock
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


def _response(status_code: int, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("NYT_BOOKS_API_KEY", "test-key")


class TestGetApiKey:
    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("NYT_BOOKS_API_KEY", "  abc  ")
        assert nyt.get_nyt_api_key() == "abc"

    def test_missing(self, monkeypatch):
        monkeypatch.delenv("NYT_BOOKS_API_KEY", raising=False)
        assert nyt.get_nyt_api_key() == ""


class TestFetchListNames:
    @pytest.mark.asyncio
    async def test_parses_and_filters(self):
        body = {
            "results": [
                {
                    "list_name": "Combined Print and E-Book Fiction",
                    "display_name": "Combined Print & E-Book Fiction",
                    "list_name_encoded": "combined-print-and-e-book-fiction",
                    "updated": "WEEKLY",
                    "oldest_published_date": "2011-02-13",
                    "newest_published_date": "2020-05-03",
                },
                {"list_name": "No Slug", "list_name_encoded": None},
            ]
        }
        get = AsyncMock(return_value=_response(200, body))
        with patch.object(nyt.httpx, "AsyncClient", _mock_async_client(get)):
            names = await nyt.fetch_list_names()
        assert len(names) == 1
        assert names[0]["list_name_encoded"] == "combined-print-and-e-book-fiction"
        assert names[0]["updated"] == "WEEKLY"

    @pytest.mark.asyncio
    async def test_no_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("NYT_BOOKS_API_KEY", raising=False)
        assert await nyt.fetch_list_names() == []


class TestFetchListCatalog:
    @pytest.mark.asyncio
    async def test_uses_names_when_available(self):
        names_body = {"results": [
            {"list_name": "Fiction", "list_name_encoded": "fiction", "updated": "WEEKLY"},
        ]}
        get = AsyncMock(return_value=_response(200, names_body))
        with patch.object(nyt.httpx, "AsyncClient", _mock_async_client(get)):
            catalog = await nyt.fetch_list_catalog()
        assert [c["list_name_encoded"] for c in catalog] == ["fiction"]
        assert get.await_count == 1  # no full-overview fallback needed

    @pytest.mark.asyncio
    async def test_falls_back_to_full_overview_on_names_failure(self):
        overview_body = {"results": {"lists": [
            {"list_name": "Fiction", "list_name_encoded": "fiction",
             "display_name": "Fiction", "updated": "WEEKLY", "books": []},
        ]}}
        get = AsyncMock(side_effect=[
            _response(429), _response(429),   # names.json + its one retry
            _response(200, overview_body),    # full-overview.json
        ])
        with patch.object(nyt.httpx, "AsyncClient", _mock_async_client(get)), \
             patch.object(nyt.asyncio, "sleep", AsyncMock()):
            catalog = await nyt.fetch_list_catalog()
        assert [c["list_name_encoded"] for c in catalog] == ["fiction"]


class TestFetchFullOverview:
    @pytest.mark.asyncio
    async def test_returns_lists(self):
        body = {"results": {"lists": [{"list_name_encoded": "x", "books": [{"primary_isbn13": "1"}]}]}}
        get = AsyncMock(return_value=_response(200, body))
        with patch.object(nyt.httpx, "AsyncClient", _mock_async_client(get)):
            lists = await nyt.fetch_full_overview()
        assert lists == [{"list_name_encoded": "x", "books": [{"primary_isbn13": "1"}]}]
        assert get.await_args.args[0].endswith("/lists/full-overview.json")

    @pytest.mark.asyncio
    async def test_falls_back_to_overview(self):
        overview_body = {"results": {"lists": [{"list_name_encoded": "y", "books": []}]}}
        get = AsyncMock(side_effect=[
            _response(404),
            _response(200, overview_body),
        ])
        with patch.object(nyt.httpx, "AsyncClient", _mock_async_client(get)):
            lists = await nyt.fetch_full_overview()
        assert lists == [{"list_name_encoded": "y", "books": []}]
        assert get.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_once_on_429(self):
        body = {"results": {"lists": []}}
        get = AsyncMock(side_effect=[_response(429), _response(200, body)])
        with patch.object(nyt.httpx, "AsyncClient", _mock_async_client(get)), \
             patch.object(nyt.asyncio, "sleep", AsyncMock()):
            lists = await nyt.fetch_full_overview()
        assert lists == []
        assert get.await_count == 2
