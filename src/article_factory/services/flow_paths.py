from __future__ import annotations

from sqlalchemy.orm import Session

DEFAULT_FLOW_PATH = "sports/sports.flow.json"


def resolve_default_flow_path(db: Session) -> str:
    from article_factory.services.runtime_settings import load_runtime_settings

    path = load_runtime_settings(db).default_flow_path.strip()
    return path or DEFAULT_FLOW_PATH
