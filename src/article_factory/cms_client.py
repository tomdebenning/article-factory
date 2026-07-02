from __future__ import annotations

from typing import Any

import httpx

from article_factory.config import settings


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

    async def put_factory_status(self, body: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{self.base_url}/internal/factory/status",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)

    async def post_run_event(self, body: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/runs/events",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)

    async def post_run_complete(self, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/internal/runs/complete",
                json=body,
                headers=self._headers(),
            )
            self._raise_for_status(response)
            return response.json()
