"""Fetch public web pages and extract readable text for factory step tools."""

from __future__ import annotations

import ipaddress
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_TIMEOUT = 15.0
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_OUTPUT_CHARS = 50_000
MAX_OUTPUT_CHARS = 100_000
USER_AGENT = "ArticleFactory/1.0"


class _TextExtractor(HTMLParser):
    SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})
    BLOCK_TAGS = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "blockquote", "pre"}
    )

    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS:
            self._skip_depth += 1
        elif lowered in self.BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif lowered in self.BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._pieces.append(f"{text} ")

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _hostname_blocked(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
    )


def validate_fetch_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        raise ValueError("url is required")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http and https URLs are allowed")
    if not parsed.netloc:
        raise ValueError("invalid URL")
    host = parsed.hostname or ""
    if _hostname_blocked(host):
        raise ValueError("URL host is not allowed")
    return parsed.geturl()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.get_text()


def extract_page_text(content_type: str | None, body: str) -> str:
    ctype = (content_type or "").lower()
    if "html" in ctype or body.lstrip().startswith("<"):
        return html_to_text(body)
    return body.strip()


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n\n[truncated]", True


async def fetch_web_page(
    url: str,
    *,
    max_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    validated = validate_fetch_url(url)
    capped_chars = max(1_000, min(int(max_chars), MAX_OUTPUT_CHARS))
    owns = client is None
    http = client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(DEFAULT_TIMEOUT),
        headers={"User-Agent": USER_AGENT},
    )
    try:
        async with http.stream("GET", validated) as response:
            response.raise_for_status()
            final_url = str(response.url)
            final_host = urlparse(final_url).hostname or ""
            if _hostname_blocked(final_host):
                raise ValueError("redirect target is not allowed")

            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"response exceeds {MAX_DOWNLOAD_BYTES} bytes")
                chunks.append(chunk)
            body_bytes = b"".join(chunks)
            charset = response.charset_encoding or "utf-8"
            try:
                body = body_bytes.decode(charset, errors="replace")
            except LookupError:
                body = body_bytes.decode("utf-8", errors="replace")

            title = ""
            if "<title" in body.lower():
                match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
                if match:
                    title = html_to_text(match.group(1)).strip()

            text = extract_page_text(response.headers.get("content-type"), body)
            formatted, truncated = truncate_text(text, capped_chars)

            return {
                "url": validated,
                "final_url": final_url,
                "status_code": response.status_code,
                "title": title,
                "content_type": response.headers.get("content-type", ""),
                "text": formatted,
                "truncated": truncated,
                "char_count": len(formatted),
            }
    finally:
        if owns:
            await http.aclose()


def format_fetch_result(payload: dict[str, Any]) -> str:
    title = payload.get("title") or ""
    lines = [f"URL: {payload.get('final_url') or payload.get('url')}"]
    if title:
        lines.append(f"Title: {title}")
    content_type = str(payload.get("content_type") or "").strip()
    if content_type:
        lines.append(f"Content-Type: {content_type}")
    if payload.get("truncated"):
        lines.append("(Content truncated to fit size limits.)")
    lines.append("")
    body = str(payload.get("text") or "").strip() or "(no readable text)"
    lines.append(body)
    return "\n".join(lines)
