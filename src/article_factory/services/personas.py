from __future__ import annotations

import re

from sqlalchemy.orm import Session

from article_factory.models import Persona


def slugify_persona_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "persona"


def _unique_slug(db: Session, base_slug: str, *, exclude_id: int | None = None) -> str:
    candidate = base_slug
    suffix = 2
    while True:
        query = db.query(Persona).filter_by(slug=candidate)
        if exclude_id is not None:
            query = query.filter(Persona.id != exclude_id)
        if query.one_or_none() is None:
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1


def _persona_payload(row: Persona) -> dict:
    return {
        "slug": row.slug,
        "name": row.name,
        "description": row.description or "",
        "style_prompt": row.style_prompt,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_personas(db: Session) -> list[dict]:
    rows = db.query(Persona).order_by(Persona.name.asc()).all()
    return [_persona_payload(row) for row in rows]


def read_persona(db: Session, slug: str) -> dict:
    row = db.query(Persona).filter_by(slug=slug).one_or_none()
    if row is None:
        raise LookupError(f"Persona not found: {slug}")
    return _persona_payload(row)


def create_persona(db: Session, body: dict) -> dict:
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("Persona name is required")
    style_prompt = str(body.get("style_prompt") or "").strip()
    if not style_prompt:
        raise ValueError("Style prompt is required")
    slug = slugify_persona_name(str(body.get("slug") or "").strip() or name)
    slug = _unique_slug(db, slug)
    row = Persona(
        slug=slug,
        name=name,
        description=str(body.get("description") or "").strip(),
        style_prompt=style_prompt,
    )
    db.add(row)
    db.flush()
    return _persona_payload(row)


def update_persona(db: Session, slug: str, body: dict) -> dict:
    row = db.query(Persona).filter_by(slug=slug).one_or_none()
    if row is None:
        raise LookupError(f"Persona not found: {slug}")

    name = str(body.get("name") or row.name).strip()
    if not name:
        raise ValueError("Persona name is required")
    style_prompt = str(body.get("style_prompt") or row.style_prompt).strip()
    if not style_prompt:
        raise ValueError("Style prompt is required")

    row.name = name
    row.description = str(body.get("description") or "").strip()
    row.style_prompt = style_prompt

    requested_slug = str(body.get("slug") or "").strip()
    if requested_slug and requested_slug != row.slug:
        row.slug = _unique_slug(db, slugify_persona_name(requested_slug), exclude_id=row.id)

    db.flush()
    return _persona_payload(row)


def delete_persona(db: Session, slug: str) -> dict:
    row = db.query(Persona).filter_by(slug=slug).one_or_none()
    if row is None:
        raise LookupError(f"Persona not found: {slug}")
    name = row.name
    db.delete(row)
    return {"ok": True, "slug": slug, "name": name}
