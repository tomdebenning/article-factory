from __future__ import annotations

from fastapi import Cookie, Header, HTTPException, Query

from article_factory.services.api_keys import is_real_api_key
from article_factory.services.factory_api_key_cache import get_cached_factory_api_key

API_KEY_COOKIE_NAME = "factory_api_key"


def provided_api_key(
    *,
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None, alias="api_key"),
    factory_api_key: str | None = Cookie(default=None, alias=API_KEY_COOKIE_NAME),
) -> str | None:
    return x_api_key or api_key or factory_api_key


def require_configured_api_key(
    *,
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None, alias="api_key"),
    factory_api_key: str | None = Cookie(default=None, alias=API_KEY_COOKIE_NAME),
) -> None:
    configured = get_cached_factory_api_key()
    if not is_real_api_key(configured):
        return
    token = provided_api_key(
        x_api_key=x_api_key,
        api_key=api_key,
        factory_api_key=factory_api_key,
    )
    if token != configured:
        raise HTTPException(status_code=401, detail="Invalid API key")
