from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from article_factory.config import settings
from article_factory.models import FactorySettings
from article_factory.services.runtime_settings import get_or_create_factory_settings

DEFAULT_FACTORY_DISPLAY_NAME = "Article Factory"


@dataclass(frozen=True)
class FactoryIdentity:
    gateway_id: str
    gateway_display_name: str


def _hostname_fallback_id() -> str:
    host = socket.gethostname().split(".")[0] or "local"
    return f"factory-{host}"


def _env_gateway_id() -> str:
    return settings.gateway_id.strip()


def _env_display_name() -> str:
    configured = settings.gateway_display_name.strip()
    return configured or DEFAULT_FACTORY_DISPLAY_NAME


def ensure_factory_gateway_id(row: FactorySettings) -> str:
    configured = (row.gateway_id or "").strip()
    if configured:
        return configured

    env_id = _env_gateway_id()
    row.gateway_id = env_id or f"factory-{uuid.uuid4().hex}"
    if not (row.gateway_display_name or "").strip():
        row.gateway_display_name = _env_display_name()
    return row.gateway_id


def load_factory_identity(db: Session) -> FactoryIdentity:
    row = get_or_create_factory_settings(db)
    gateway_id = ensure_factory_gateway_id(row)
    display_name = (row.gateway_display_name or "").strip() or _env_display_name()
    if not (row.gateway_display_name or "").strip():
        row.gateway_display_name = display_name
    db.commit()
    db.refresh(row)
    return FactoryIdentity(gateway_id=gateway_id, gateway_display_name=display_name)


def save_factory_display_name(db: Session, display_name: str) -> FactoryIdentity:
    clean = display_name.strip()
    if not clean:
        raise ValueError("gateway_display_name required")
    row = get_or_create_factory_settings(db)
    ensure_factory_gateway_id(row)
    row.gateway_display_name = clean
    db.commit()
    db.refresh(row)
    return FactoryIdentity(gateway_id=row.gateway_id, gateway_display_name=row.gateway_display_name)
