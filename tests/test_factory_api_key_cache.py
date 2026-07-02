from __future__ import annotations

from article_factory.config import settings
from article_factory.services.factory_api_key_cache import (
    get_cached_factory_api_key,
    invalidate_factory_api_key_cache,
    warm_factory_api_key_cache,
)
from article_factory.services.runtime_settings import set_factory_api_key


def test_warm_cache_from_db(configured_db) -> None:
    import article_factory.db as db_module

    invalidate_factory_api_key_cache()
    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "cached-secret")
    finally:
        db.close()

    assert get_cached_factory_api_key() == "cached-secret"


def test_warm_cache_prefers_env(monkeypatch, configured_db) -> None:
    import article_factory.db as db_module

    invalidate_factory_api_key_cache()
    monkeypatch.setattr(settings, "factory_api_key", "env-secret")

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "db-secret")
    finally:
        db.close()

    invalidate_factory_api_key_cache()
    assert warm_factory_api_key_cache() == "env-secret"


def test_set_factory_api_key_refreshes_cache(configured_db) -> None:
    import article_factory.db as db_module

    invalidate_factory_api_key_cache()
    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "first-key")
        assert get_cached_factory_api_key() == "first-key"
        set_factory_api_key(db, "second-key")
        assert get_cached_factory_api_key() == "second-key"
    finally:
        db.close()
