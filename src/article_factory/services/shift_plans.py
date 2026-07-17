from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import ShiftAssignment, ShiftDeskSlot, ShiftPlan
from article_factory.services.flow_paths import resolve_default_flow_path
from article_factory.services.runtime_settings import load_runtime_settings
from article_factory.services.shift_windows import ShiftWindow, today_and_tomorrow_shift_windows


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _assignment_counts(db: Session, plan_id: int) -> dict[str, int]:
    rows = (
        db.query(ShiftAssignment.status)
        .join(ShiftDeskSlot, ShiftAssignment.shift_desk_slot_id == ShiftDeskSlot.id)
        .filter(ShiftDeskSlot.shift_plan_id == plan_id)
        .all()
    )
    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    for (status,) in rows:
        key = status if status in counts else "pending"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _desk_payload(db: Session, slot: ShiftDeskSlot) -> dict[str, Any]:
    rows = (
        db.query(ShiftAssignment)
        .filter_by(shift_desk_slot_id=slot.id)
        .order_by(ShiftAssignment.priority, ShiftAssignment.id)
        .all()
    )
    tally = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    for row in rows:
        key = row.status if row.status in tally else "pending"
        tally[key] = tally.get(key, 0) + 1
    return {
        "id": slot.id,
        "shift_plan_id": slot.shift_plan_id,
        "name": slot.name,
        "desk_path": slot.desk_path,
        "topic_slug": slot.topic_slug,
        "flow_version_id": slot.flow_version_id,
        "dispatch_order": slot.dispatch_order,
        "reporter_selection_mode": slot.reporter_selection_mode,
        "assignment_counts": tally,
        "assignment_total": len(rows),
        "assignments": [
            {
                "id": row.id,
                "prompt": row.prompt,
                "status": row.status,
                "priority": row.priority,
                "run_id": row.run_id,
            }
            for row in rows
        ],
    }


def shift_plan_payload(db: Session, plan: ShiftPlan) -> dict[str, Any]:
    desks = (
        db.query(ShiftDeskSlot)
        .filter_by(shift_plan_id=plan.id)
        .order_by(ShiftDeskSlot.dispatch_order, ShiftDeskSlot.id)
        .all()
    )
    counts = _assignment_counts(db, plan.id)
    return {
        "id": plan.id,
        "shift_key": plan.shift_key,
        "window_starts_at": plan.window_starts_at.isoformat(),
        "window_ends_at": plan.window_ends_at.isoformat(),
        "status": plan.status,
        "default_model": plan.default_model,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "activated_at": plan.activated_at.isoformat() if plan.activated_at else None,
        "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        "assignment_counts": counts,
        "assignment_total": sum(counts.values()),
        "desks": [_desk_payload(db, desk) for desk in desks],
    }


def list_shift_board(db: Session) -> list[dict[str, Any]]:
    windows = today_and_tomorrow_shift_windows()
    board: list[dict[str, Any]] = []
    for window in windows:
        plan = (
            db.query(ShiftPlan)
            .filter_by(window_starts_at=_utc(window.starts_at))
            .one_or_none()
        )
        entry: dict[str, Any] = {
            "window_key": window.window_key,
            "shift_key": window.shift_key,
            "label": window.label,
            "window_starts_at": window.starts_at.isoformat(),
            "window_ends_at": window.ends_at.isoformat(),
            "plan": shift_plan_payload(db, plan) if plan else None,
        }
        board.append(entry)
    return board


def get_or_create_shift_plan(db: Session, window: ShiftWindow) -> ShiftPlan:
    starts = _utc(window.starts_at)
    plan = db.query(ShiftPlan).filter_by(window_starts_at=starts).one_or_none()
    if plan is not None:
        return plan
    runtime = load_runtime_settings(db)
    plan = ShiftPlan(
        shift_key=window.shift_key,
        window_starts_at=starts,
        window_ends_at=_utc(window.ends_at),
        status="draft",
        default_model=(runtime.default_model or "").strip(),
    )
    db.add(plan)
    db.flush()
    return plan


def get_shift_plan(db: Session, plan_id: int) -> ShiftPlan:
    plan = db.get(ShiftPlan, plan_id)
    if plan is None:
        raise LookupError(f"Shift plan not found: {plan_id}")
    return plan


def add_desk_slot(
    db: Session,
    *,
    plan_id: int,
    desk_path: str,
    topic_slug: str = "general",
    name: str = "",
    flow_version_id: int | None = None,
    reporter_selection_mode: str = "round_robin",
) -> ShiftDeskSlot:
    plan = get_shift_plan(db, plan_id)
    if plan.status == "complete":
        raise ValueError("Cannot edit a completed shift")
    if plan.status == "active":
        raise ValueError("Stop the shift before editing desk staffing")
    cleaned_path = (desk_path or "").strip() or resolve_default_flow_path(db)
    max_order = (
        db.query(ShiftDeskSlot.dispatch_order)
        .filter_by(shift_plan_id=plan.id)
        .order_by(ShiftDeskSlot.dispatch_order.desc())
        .first()
    )
    next_order = (max_order[0] + 100) if max_order else 100
    mode = (reporter_selection_mode or "round_robin").strip().lower()
    if mode not in {"round_robin", "lru"}:
        mode = "round_robin"
    slot = ShiftDeskSlot(
        shift_plan_id=plan.id,
        name=(name or "").strip() or cleaned_path,
        desk_path=cleaned_path,
        topic_slug=(topic_slug or "general").strip() or "general",
        flow_version_id=flow_version_id,
        dispatch_order=next_order,
        reporter_selection_mode=mode,
    )
    db.add(slot)
    db.flush()
    return slot


def replace_desk_assignments(
    db: Session,
    *,
    desk_slot_id: int,
    prompts: list[str],
    priority: int = 100,
) -> list[ShiftAssignment]:
    slot = db.get(ShiftDeskSlot, desk_slot_id)
    if slot is None:
        raise LookupError(f"Desk slot not found: {desk_slot_id}")
    plan = get_shift_plan(db, slot.shift_plan_id)
    if plan.status != "draft":
        raise ValueError("Assignments can only be edited while the shift is a draft")
    active = (
        db.query(ShiftAssignment)
        .filter_by(shift_desk_slot_id=slot.id)
        .filter(ShiftAssignment.status.in_(("running",)))
        .count()
    )
    if active:
        raise ValueError("Cannot replace assignments while desk work is running")

    db.query(ShiftAssignment).filter_by(shift_desk_slot_id=slot.id).delete(
        synchronize_session=False
    )
    created: list[ShiftAssignment] = []
    for index, line in enumerate(prompts):
        prompt = line.strip()
        if not prompt:
            continue
        row = ShiftAssignment(
            shift_desk_slot_id=slot.id,
            prompt=prompt,
            priority=priority + index,
            status="pending",
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def update_shift_plan_settings(
    db: Session,
    plan_id: int,
    *,
    default_model: str | None = None,
) -> ShiftPlan:
    plan = get_shift_plan(db, plan_id)
    if plan.status == "complete":
        raise ValueError("Cannot edit a completed shift")
    if default_model is not None:
        cleaned = default_model.strip()
        if not cleaned:
            raise ValueError("Select a model for this shift")
        plan.default_model = cleaned
    db.flush()
    return plan


def activate_shift_plan(db: Session, plan_id: int) -> ShiftPlan:
    plan = get_shift_plan(db, plan_id)
    if plan.status == "active":
        return plan
    if plan.status == "complete":
        raise ValueError("Shift is already complete")
    counts = _assignment_counts(db, plan.id)
    total = sum(counts.values())
    if total < 1:
        raise ValueError("Add at least one assignment before activating the shift")
    if not (plan.default_model or "").strip():
        runtime = load_runtime_settings(db)
        model = (runtime.default_model or "").strip()
        if not model:
            raise ValueError("Select a model before activating the shift")
        plan.default_model = model
    active_other = db.query(ShiftPlan).filter_by(status="active").filter(ShiftPlan.id != plan.id).first()
    if active_other is not None:
        raise ValueError("Another shift is already active — complete it before starting a new one")
    plan.status = "active"
    plan.activated_at = datetime.now(timezone.utc)
    db.flush()
    return plan


def maybe_complete_shift_plan(db: Session, plan_id: int) -> ShiftPlan | None:
    plan = db.get(ShiftPlan, plan_id)
    if plan is None or plan.status != "active":
        return plan
    pending_or_running = (
        db.query(ShiftAssignment.id)
        .join(ShiftDeskSlot, ShiftAssignment.shift_desk_slot_id == ShiftDeskSlot.id)
        .filter(
            ShiftDeskSlot.shift_plan_id == plan.id,
            ShiftAssignment.status.in_(("pending", "running")),
        )
        .limit(1)
        .first()
    )
    if pending_or_running is not None:
        return plan
    total = sum(_assignment_counts(db, plan.id).values())
    if total < 1:
        return plan
    plan.status = "complete"
    plan.completed_at = datetime.now(timezone.utc)
    db.flush()
    return plan


def list_assignments_for_desk(db: Session, desk_slot_id: int) -> list[dict[str, Any]]:
    slot = db.get(ShiftDeskSlot, desk_slot_id)
    if slot is None:
        raise LookupError(f"Desk slot not found: {desk_slot_id}")
    rows = (
        db.query(ShiftAssignment)
        .filter_by(shift_desk_slot_id=desk_slot_id)
        .order_by(ShiftAssignment.priority, ShiftAssignment.id)
        .all()
    )
    return [
        {
            "id": row.id,
            "prompt": row.prompt,
            "status": row.status,
            "priority": row.priority,
            "run_id": row.run_id,
            "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in rows
    ]
