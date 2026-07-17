from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from article_factory.models import ShiftAssignment, ShiftDeskSlot, ShiftPlan
from article_factory.services.shift_plans import maybe_complete_shift_plan


def select_pending_assignments_round_robin(
    db: Session,
    *,
    limit: int,
    start_index: int = 0,
) -> tuple[list[tuple[ShiftAssignment, ShiftDeskSlot, ShiftPlan]], int]:
    """Pick pending assignments from the active shift, round-robin across desk slots."""
    if limit <= 0:
        return [], start_index

    active_plan = db.query(ShiftPlan).filter_by(status="active").order_by(ShiftPlan.activated_at.desc()).first()
    if active_plan is None:
        return [], start_index

    desks = (
        db.query(ShiftDeskSlot)
        .filter_by(shift_plan_id=active_plan.id)
        .order_by(ShiftDeskSlot.dispatch_order, ShiftDeskSlot.id)
        .all()
    )
    if not desks:
        return [], start_index

    picked: list[tuple[ShiftAssignment, ShiftDeskSlot, ShiftPlan]] = []
    picked_ids: set[int] = set()
    index = start_index % len(desks)
    attempts = 0
    max_attempts = len(desks) * max(limit, 1)

    while len(picked) < limit and attempts < max_attempts:
        desk = desks[index]
        query = (
            db.query(ShiftAssignment)
            .filter_by(shift_desk_slot_id=desk.id, status="pending")
            .order_by(ShiftAssignment.priority, ShiftAssignment.id)
        )
        if picked_ids:
            query = query.filter(~ShiftAssignment.id.in_(picked_ids))
        assignment = query.first()
        if assignment is not None:
            picked.append((assignment, desk, active_plan))
            picked_ids.add(assignment.id)
        index = (index + 1) % len(desks)
        attempts += 1

    next_index = index if desks else 0
    return picked, next_index


def mark_assignment_status(
    db: Session,
    *,
    assignment_id: int | None,
    status: str,
    run_id: str | None = None,
) -> None:
    if assignment_id is None:
        return
    assignment = db.get(ShiftAssignment, assignment_id)
    if assignment is None:
        return
    assignment.status = status
    if run_id:
        assignment.run_id = run_id
    now = datetime.now(timezone.utc)
    if status == "running" and assignment.dispatched_at is None:
        assignment.dispatched_at = now
    if status in {"completed", "failed"}:
        assignment.finished_at = now
    slot = db.get(ShiftDeskSlot, assignment.shift_desk_slot_id)
    if slot is not None:
        maybe_complete_shift_plan(db, slot.shift_plan_id)
