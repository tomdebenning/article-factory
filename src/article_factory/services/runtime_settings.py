from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from article_factory.config import settings
from article_factory.models import FactorySettings


from article_factory.services.flow_paths import DEFAULT_FLOW_PATH


@dataclass(frozen=True)
class RuntimeSettings:
    control_plane_url: str
    cms_url: str
    cms_api_key: str
    default_puller: str
    default_model: str
    default_flow_path: str = DEFAULT_FLOW_PATH
    brave_search_api_key: str = ""
    display_timezone: str = "UTC"
    auto_scheduler_enabled: bool = True


from article_factory.services.api_keys import is_real_api_key, normalize_api_key
from article_factory.services.factory_api_key_cache import (
    invalidate_factory_api_key_cache,
    warm_factory_api_key_cache,
)


def _fallback(value: str, env_value: str) -> str:
    stripped = normalize_api_key(value)
    if is_real_api_key(stripped):
        return stripped
    stripped_env = normalize_api_key(env_value)
    if is_real_api_key(stripped_env):
        return stripped_env
    return ""


def normalize_base_url(url: str) -> str:
    """Ensure integration URLs include a scheme (users often enter host:port only)."""
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return cleaned
    if "://" not in cleaned:
        return f"http://{cleaned}"
    return cleaned


def get_or_create_factory_settings(db: Session) -> FactorySettings:
    row = db.get(FactorySettings, 1)
    if row is None:
        row = FactorySettings(
            id=1,
            control_plane_url=settings.control_plane_url,
            cms_url=settings.cms_url,
            cms_api_key=settings.cms_api_key,
            default_puller=settings.default_puller,
            default_model=settings.default_model,
            default_flow_path=DEFAULT_FLOW_PATH,
            brave_search_api_key=settings.brave_search_api_key,
        )
        db.add(row)
        db.flush()
    return row


def get_effective_factory_api_key(db: Session) -> str:
    row = get_or_create_factory_settings(db)
    return _fallback(row.factory_api_key, settings.factory_api_key)


def set_factory_api_key(db: Session, api_key: str) -> FactorySettings:
    row = get_or_create_factory_settings(db)
    row.factory_api_key = api_key.strip()
    db.commit()
    db.refresh(row)
    warm_factory_api_key_cache(db)
    return row


def load_runtime_settings(db: Session) -> RuntimeSettings:
    row = get_or_create_factory_settings(db)
    return RuntimeSettings(
        control_plane_url=_fallback(row.control_plane_url, settings.control_plane_url),
        cms_url=_fallback(row.cms_url, settings.cms_url),
        cms_api_key=_fallback(row.cms_api_key, settings.cms_api_key),
        default_puller=_fallback(row.default_puller, settings.default_puller),
        default_model=_fallback(row.default_model, settings.default_model),
        default_flow_path=(row.default_flow_path or DEFAULT_FLOW_PATH).strip() or DEFAULT_FLOW_PATH,
        brave_search_api_key=_fallback(row.brave_search_api_key, settings.brave_search_api_key),
        display_timezone=(getattr(row, "display_timezone", None) or "UTC").strip() or "UTC",
        auto_scheduler_enabled=bool(getattr(row, "auto_scheduler_enabled", True)),
    )


def update_factory_settings(db: Session, body: dict[str, str]) -> FactorySettings:
    row = get_or_create_factory_settings(db)
    for field in (
        "control_plane_url",
        "cms_url",
        "cms_api_key",
        "default_puller",
        "default_model",
        "default_flow_path",
        "brave_search_api_key",
        "display_timezone",
    ):
        if field in body:
            value = body[field].strip()
            if field in ("control_plane_url", "cms_url"):
                value = normalize_base_url(value)
            setattr(row, field, value)
    if "auto_scheduler_enabled" in body:
        row.auto_scheduler_enabled = bool(body["auto_scheduler_enabled"])
    db.commit()
    db.refresh(row)
    return row
