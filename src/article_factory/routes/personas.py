from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.routes.admin import require_api_key
from article_factory.schemas import PersonaBody
from article_factory.services.personas import (
    create_persona,
    delete_persona,
    list_personas,
    read_persona,
    update_persona,
)

router = APIRouter(prefix="/api/personas", dependencies=[Depends(require_api_key)])


@router.get("")
def get_personas(db: Session = Depends(get_db)) -> dict:
    return {"personas": list_personas(db)}


@router.post("")
def post_persona(body: PersonaBody, db: Session = Depends(get_db)) -> dict:
    try:
        persona = create_persona(db, body.model_dump())
        db.commit()
        return {"persona": persona}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{slug}")
def get_persona(slug: str, db: Session = Depends(get_db)) -> dict:
    try:
        return {"persona": read_persona(db, slug)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{slug}")
def put_persona(slug: str, body: PersonaBody, db: Session = Depends(get_db)) -> dict:
    try:
        persona = update_persona(db, slug, body.model_dump())
        db.commit()
        return {"persona": persona}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{slug}")
def remove_persona(slug: str, db: Session = Depends(get_db)) -> dict:
    try:
        result = delete_persona(db, slug)
        db.commit()
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
