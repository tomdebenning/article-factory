from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from article_factory.services.step_tools import StepToolRegistry
from article_factory.services.web_fetch import (
    extract_page_text,
    format_fetch_result,
    html_to_text,
    validate_fetch_url,
)


def test_validate_fetch_url_rejects_private_hosts() -> None:
    with pytest.raises(ValueError):
        validate_fetch_url("http://127.0.0.1/page")
    with pytest.raises(ValueError):
        validate_fetch_url("http://localhost/page")


def test_validate_fetch_url_requires_http_scheme() -> None:
    with pytest.raises(ValueError):
        validate_fetch_url("file:///etc/passwd")


def test_html_to_text_strips_scripts() -> None:
    text = html_to_text("<html><script>bad()</script><body><p>Hello</p></body></html>")
    assert "bad()" not in text
    assert "Hello" in text


def test_extract_page_text_plain() -> None:
    assert extract_page_text("text/plain", "plain body") == "plain body"


def test_format_fetch_result_includes_body() -> None:
    formatted = format_fetch_result(
        {
            "url": "https://example.com",
            "final_url": "https://example.com/page",
            "title": "Example",
            "text": "Page body",
        }
    )
    assert "Example" in formatted
    assert "Page body" in formatted


@pytest.mark.asyncio
async def test_web_fetch_tool_executes() -> None:
    registry = StepToolRegistry(workspace_root=__import__("pathlib").Path("/tmp/unused"), brave_api_key="")
    with patch(
        "article_factory.services.step_tools.fetch_web_page",
        new=AsyncMock(
            return_value={
                "url": "https://example.com",
                "final_url": "https://example.com",
                "title": "Example",
                "content_type": "text/html",
                "text": "Fetched content",
                "truncated": False,
            }
        ),
    ):
        result = await registry.execute(
            {
                "id": "call-fetch",
                "function": {"name": "web_fetch", "arguments": {"url": "https://example.com"}},
            }
        )
    assert "Fetched content" in result["content"]
    assert result["name"] == "web_fetch"
