from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx

from article_factory.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CmsRequestError(Exception):
    """Readable error from Showroom CMS HTTP calls."""


def cms_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return f"Showroom CMS: {detail}"
        if detail is not None:
            return f"Showroom CMS: {detail}"
    except Exception:
        pass
    return f"Showroom CMS error {response.status_code} for {response.request.method} {response.request.url.path}"


def cms_http_verify(base_url: str) -> bool:
    """Allow self-signed TLS for local Showroom dev servers."""
    host = (urlparse(base_url).hostname or "").lower()
    return host not in {"127.0.0.1", "localhost", "::1"}


async def best_effort_showroom(
    action: str,
    call: Callable[[], Awaitable[T]],
    *,
    default: T | None = None,
) -> T | None:
    """Run a Showroom CMS call without blocking the factory write path."""
    try:
        return await call()
    except Exception as exc:
        logger.warning("Showroom %s failed (factory continues): %s", action, exc)
        return default


class CmsClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.cms_url).rstrip("/")
        self.api_key = api_key or settings.cms_api_key

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CmsRequestError(cms_error_message(response)) from exc

    def _client(self, *, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, verify=cms_http_verify(self.base_url))

    async def put_factory_status(self, body: dict[str, Any]) -> None:
        async with self._client(timeout=30.0) as client:
            response = await client.put(
                f"{self.base_url}/internal/factory/status",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)

    async def post_run_event(self, body: dict[str, Any]) -> None:
        async with self._client(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/runs/events",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)

    async def post_run_complete(self, body: dict[str, Any]) -> dict[str, Any]:
        async with self._client(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/runs/complete",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)
            return response.json()

    async def post_flow_batch_complete(self, body: dict[str, Any]) -> dict[str, Any]:
        async with self._client(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/flows/batch-complete",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)
            return response.json()
