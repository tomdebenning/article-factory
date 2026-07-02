from __future__ import annotations

from sqlalchemy.orm import Session

from article_factory.config import settings
from article_factory.services.api_keys import is_real_api_key, normalize_api_key

_cached_factory_api_key: str | None = None
_cache_ready = False


def invalidate_factory_api_key_cache() -> None:
    global _cached_factory_api_key, _cache_ready
    _cached_factory_api_key = None
    _cache_ready = False


def _resolve_factory_api_key(*, db_value: str, env_value: str) -> str:
    stripped = normalize_api_key(db_value)
    if is_real_api_key(stripped):
        return stripped
    stripped_env = normalize_api_key(env_value)
    if is_real_api_key(stripped_env):
        return stripped_env
    return ""


def warm_factory_api_key_cache(db: Session | None = None) -> str:
    """Load the effective factory API key once; subsequent auth checks avoid the DB."""
    global _cached_factory_api_key, _cache_ready

    env_key = _resolve_factory_api_key(db_value="", env_value=settings.factory_api_key)
    if env_key:
        _cached_factory_api_key = env_key
        _cache_ready = True
        return env_key

    if db is not None:
        from article_factory.services.runtime_settings import get_or_create_factory_settings

        row = get_or_create_factory_settings(db)
        resolved = _resolve_factory_api_key(
            db_value=row.factory_api_key,
            env_value=settings.factory_api_key,
        )
        _cached_factory_api_key = resolved
        _cache_ready = True
        return resolved

    _cached_factory_api_key = ""
    _cache_ready = True
    return ""


def get_cached_factory_api_key() -> str:
    if not _cache_ready:
        return warm_factory_api_key_cache()
    return _cached_factory_api_key or ""
