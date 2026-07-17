"""Select desk staff (reporter personas) from a desk pool for shift assignments."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from article_factory.models import Persona, ShiftAssignment, ShiftDeskSlot


def merge_persona_style_prompt(base_prompt: str, style_prompt: str) -> str:
    base = (base_prompt or "").strip()
    style = (style_prompt or "").strip()
    if not style:
        return base
    if not base:
        return style
    return f"{base.rstrip()}\n\n{style}"


def load_reporter_pool(db: Session, *, desk_path: str, flow_version_id: int | None) -> list[str]:
    from article_factory.models import FactoryRun
    from article_factory.services.flow_versions import resolve_flow_for_run

    preview = FactoryRun(flow_path=desk_path, flow_version_id=flow_version_id)
    try:
        flow = resolve_flow_for_run(db, preview)
    except Exception:
        try:
            from article_factory.services.flow_storage import read_flow

            flow = read_flow(desk_path)
        except Exception:
            return []

    pool = [slug.strip() for slug in (flow.reporter_pool or []) if str(slug).strip()]
    if not pool:
        return []
    existing = {
        row.slug
        for row in db.query(Persona.slug).filter(Persona.slug.in_(pool)).all()
    }
    return [slug for slug in pool if slug in existing]


def _assignments_for_desk_in_shift(
    db: Session,
    *,
    shift_plan_id: int,
    desk_slot_id: int,
) -> list[ShiftAssignment]:
    return (
        db.query(ShiftAssignment)
        .join(ShiftDeskSlot, ShiftAssignment.shift_desk_slot_id == ShiftDeskSlot.id)
        .filter(
            ShiftDeskSlot.shift_plan_id == shift_plan_id,
            ShiftAssignment.shift_desk_slot_id == desk_slot_id,
            ShiftAssignment.reporter_persona_slug.isnot(None),
            ShiftAssignment.status.in_(("running", "completed", "failed")),
        )
        .order_by(ShiftAssignment.dispatched_at.asc(), ShiftAssignment.id.asc())
        .all()
    )


def select_reporter_persona_slug(
    db: Session,
    *,
    pool: list[str],
    mode: str,
    shift_plan_id: int,
    desk_slot_id: int,
) -> str | None:
    if not pool:
        return None

    cleaned_mode = (mode or "round_robin").strip().lower()
    if cleaned_mode not in {"round_robin", "lru"}:
        cleaned_mode = "round_robin"

    if cleaned_mode == "round_robin":
        prior = _assignments_for_desk_in_shift(
            db, shift_plan_id=shift_plan_id, desk_slot_id=desk_slot_id
        )
        index = len(prior) % len(pool)
        return pool[index]

    # Least recently used within this desk slot on the shift.
    last_used: dict[str, datetime] = {slug: datetime.min.replace(tzinfo=timezone.utc) for slug in pool}
    for row in _assignments_for_desk_in_shift(db, shift_plan_id=shift_plan_id, desk_slot_id=desk_slot_id):
        slug = (row.reporter_persona_slug or "").strip()
        if slug not in last_used:
            continue
        stamp = row.dispatched_at or row.finished_at or row.created_at
        if stamp and stamp > last_used[slug]:
            last_used[slug] = stamp

    return min(pool, key=lambda slug: (last_used.get(slug, datetime.min.replace(tzinfo=timezone.utc)), slug))


def assign_reporter_to_assignment(
    db: Session,
    *,
    assignment: ShiftAssignment,
    desk_slot: ShiftDeskSlot,
    shift_plan_id: int,
) -> str | None:
    if (assignment.reporter_persona_slug or "").strip():
        return assignment.reporter_persona_slug

    pool = load_reporter_pool(
        db,
        desk_path=desk_slot.desk_path,
        flow_version_id=desk_slot.flow_version_id,
    )
    slug = select_reporter_persona_slug(
        db,
        pool=pool,
        mode=desk_slot.reporter_selection_mode,
        shift_plan_id=shift_plan_id,
        desk_slot_id=desk_slot.id,
    )
    if slug:
        assignment.reporter_persona_slug = slug
        db.flush()
    return slug


def persona_display_name(db: Session, slug: str | None) -> str | None:
    cleaned = (slug or "").strip()
    if not cleaned:
        return None
    row = db.query(Persona).filter_by(slug=cleaned).one_or_none()
    return row.name if row else cleaned
