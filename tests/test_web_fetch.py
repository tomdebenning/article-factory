from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from article_factory.services.step_tools import StepToolRegistry
from article_factory.services.web_fetch import (
    _hostname_blocked,
    extract_page_text,
    fetch_web_page,
    format_fetch_result,
    html_to_text,
    truncate_text,
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
    with pytest.raises(ValueError):
        validate_fetch_url("")
    with pytest.raises(ValueError):
        validate_fetch_url("http:///path")


def test_hostname_blocked_private_and_local() -> None:
    assert _hostname_blocked("10.0.0.1")
    assert _hostname_blocked("localhost")
    with pytest.raises(ValueError):
        validate_fetch_url("https://foo.local/page")


def test_html_to_text_strips_scripts() -> None:
    text = html_to_text("<html><script>bad()</script><body><p>Hello</p></body></html>")
    assert "bad()" not in text
    assert "Hello" in text


def test_extract_page_text_plain() -> None:
    assert extract_page_text("text/plain", "plain body") == "plain body"


def test_extract_page_text_html_by_body_prefix() -> None:
    assert "Hello" in extract_page_text("text/plain", "<p>Hello</p>")


def test_truncate_text_adds_marker() -> None:
    text, truncated = truncate_text("x" * 200, 50)
    assert truncated is True
    assert "[truncated]" in text


def test_format_fetch_result_includes_truncated_and_content_type() -> None:
    formatted = format_fetch_result(
        {
            "url": "https://example.com",
            "final_url": "https://example.com/page",
            "title": "Example",
            "content_type": "text/html",
            "text": "Page body",
            "truncated": True,
        }
    )
    assert "Content-Type: text/html" in formatted
    assert "truncated" in formatted.lower()
    assert "Page body" in formatted


@pytest.mark.asyncio
async def test_fetch_web_page_success() -> None:
    class FakeStreamResponse:
        status_code = 200
        url = "https://example.com/page"
        charset_encoding = "utf-8"

        def __init__(self) -> None:
            self.headers = {"content-type": "text/html; charset=utf-8"}

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b"<html><head><title>Example</title></head><body><p>Hello</p></body></html>"

    class FakeClient:
        def stream(self, method: str, url: str):
            assert method == "GET"
            return _FakeStreamContext(FakeStreamResponse())

        async def aclose(self) -> None:
            return None

    class _FakeStreamContext:
        def __init__(self, response: FakeStreamResponse) -> None:
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, *args):
            return False

    result = await fetch_web_page("https://example.com", client=FakeClient())  # type: ignore[arg-type]
    assert result["title"] == "Example"
    assert "Hello" in result["text"]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_fetch_web_page_blocks_redirect_to_private_host() -> None:
    class FakeStreamResponse:
        status_code = 200
        url = "http://127.0.0.1/secret"
        charset_encoding = "utf-8"
        headers = {"content-type": "text/plain"}

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b"secret"

    class FakeClient:
        def stream(self, method: str, url: str):
            return _FakeStreamContext(FakeStreamResponse())

        async def aclose(self) -> None:
            return None

    class _FakeStreamContext:
        def __init__(self, response: FakeStreamResponse) -> None:
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, *args):
            return False

    with pytest.raises(ValueError, match="redirect target"):
        await fetch_web_page("https://example.com", client=FakeClient())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_web_page_rejects_oversized_response() -> None:
    from article_factory.services import web_fetch as web_fetch_module

    class FakeStreamResponse:
        status_code = 200
        url = "https://example.com/big"
        charset_encoding = "utf-8"
        headers = {"content-type": "text/plain"}

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            chunk = b"x" * (web_fetch_module.MAX_DOWNLOAD_BYTES // 2 + 1)
            yield chunk
            yield chunk

    class FakeClient:
        def stream(self, method: str, url: str):
            return _FakeStreamContext(FakeStreamResponse())

        async def aclose(self) -> None:
            return None

    class _FakeStreamContext:
        def __init__(self, response: FakeStreamResponse) -> None:
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, *args):
            return False

    with pytest.raises(ValueError, match="exceeds"):
        await fetch_web_page("https://example.com/big", client=FakeClient())  # type: ignore[arg-type]


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
