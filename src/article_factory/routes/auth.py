from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.services.api_key_auth import require_configured_api_key
from article_factory.services.api_keys import generate_api_key, is_real_api_key, mask_api_key
from article_factory.services.factory_api_key_cache import get_cached_factory_api_key
from article_factory.services.runtime_settings import get_effective_factory_api_key, set_factory_api_key

router = APIRouter(prefix="/api/auth")


def require_factory_api_key(
    x_api_key: str | None = Header(default=None),
    factory_api_key: str | None = Cookie(default=None, alias="factory_api_key"),
) -> None:
    require_configured_api_key(x_api_key=x_api_key, factory_api_key=factory_api_key)


@router.get("")
def auth_status(db: Session = Depends(get_db)) -> dict:
    configured_key = get_effective_factory_api_key(db)
    configured = is_real_api_key(configured_key)
    return {
        "configured": configured,
        "masked": mask_api_key(configured_key) if configured else None,
    }


@router.post("/generate", dependencies=[Depends(require_factory_api_key)])
def generate_auth_key(db: Session = Depends(get_db)) -> dict:
    new_key = generate_api_key()
    set_factory_api_key(db, new_key)
    return {
        "api_key": new_key,
        "configured": True,
        "message": "Factory admin API key generated. Copy it now — it will not be shown again in full.",
    }
