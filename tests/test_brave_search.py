from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from article_factory.services.brave_search import (
    brave_web_search,
    format_brave_results,
)


@pytest.mark.asyncio
async def test_brave_web_search_requires_api_key() -> None:
    with pytest.raises(ValueError, match="not configured"):
        await brave_web_search(api_key="", query="test")


@pytest.mark.asyncio
async def test_brave_web_search_single_page() -> None:
    page_response = MagicMock()
    page_response.raise_for_status = MagicMock()
    page_response.json.return_value = {
        "web": {"results": [{"title": "One", "url": "https://a.test", "description": "A"}]},
        "query": {"more_results_available": False},
    }

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=page_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.services.brave_search.httpx.AsyncClient", return_value=mock_http):
        payload = await brave_web_search(api_key="key", query="article", count=5)

    assert len(payload["web"]["results"]) == 1
    assert payload["query"]["requested_count"] == 5
    assert payload["query"]["returned_count"] == 1
    assert mock_http.get.await_count == 1


@pytest.mark.asyncio
async def test_brave_web_search_paginates_when_more_available() -> None:
    page_one = MagicMock()
    page_one.raise_for_status = MagicMock()
    page_one.json.return_value = {
        "web": {
            "results": [
                {"title": f"Hit {index}", "url": f"https://a.test/{index}", "description": "d"}
                for index in range(20)
            ]
        },
        "query": {"more_results_available": True},
    }
    page_two = MagicMock()
    page_two.raise_for_status = MagicMock()
    page_two.json.return_value = {
        "web": {"results": [{"title": "Last", "url": "https://a.test/last", "description": "d"}]},
        "query": {"more_results_available": False},
    }

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=[page_one, page_two])
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.services.brave_search.httpx.AsyncClient", return_value=mock_http):
        payload = await brave_web_search(api_key="key", query="article", count=25)

    assert len(payload["web"]["results"]) == 21
    assert payload["query"]["returned_count"] == 21
    assert mock_http.get.await_count == 2


@pytest.mark.asyncio
async def test_brave_web_search_stops_on_empty_page() -> None:
    page_one = MagicMock()
    page_one.raise_for_status = MagicMock()
    page_one.json.return_value = {
        "web": {"results": [{"title": "Only", "url": "https://a.test", "description": "d"}]},
        "query": {"more_results_available": True},
    }
    page_two = MagicMock()
    page_two.raise_for_status = MagicMock()
    page_two.json.return_value = {"web": {"results": []}, "query": {"more_results_available": True}}

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=[page_one, page_two])
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.services.brave_search.httpx.AsyncClient", return_value=mock_http):
        payload = await brave_web_search(api_key="key", query="article", count=10)

    assert len(payload["web"]["results"]) == 1
    assert payload["query"]["more_results_available"] is False


def test_format_brave_results_empty() -> None:
    assert format_brave_results({"web": {"results": []}}) == "No results found."


def test_format_brave_results_partial_footer() -> None:
    payload = {
        "web": {"results": [{"title": "T", "url": "https://a.test", "description": "d"}]},
        "query": {"requested_count": 10, "returned_count": 1},
    }
    formatted = format_brave_results(payload)
    assert "Requested 10" in formatted
    assert "Brave returned 1" in formatted


@pytest.mark.asyncio
async def test_brave_web_search_raises_on_http_error() -> None:
    page_response = MagicMock()
    page_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401",
        request=MagicMock(),
        response=MagicMock(status_code=401),
    )

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=page_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.services.brave_search.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(httpx.HTTPStatusError):
            await brave_web_search(api_key="bad-key", query="article")
