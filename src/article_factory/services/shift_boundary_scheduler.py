"""Auto activate and hard-stop shifts at UTC window boundaries."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import FactoryRun, ShiftAssignment, ShiftDeskSlot, ShiftPlan, StandingOrder
from article_factory.services.assignment_desk import (
    T15_LEAD_MINUTES,
    apply_standing_to_desk,
    get_standing_order,
    run_t15_for_plan,
)
from article_factory.services.newsroom_alerts import create_alert, resolve_alerts_for_plan
from article_factory.services.puller_selection import idle_pullers_for_model
from article_factory.services.runtime_settings import get_or_create_factory_settings, load_runtime_settings
from article_factory.services.shift_plans import (
    _assignment_counts,
    activate_shift_plan,
    add_desk_slot,
    get_or_create_shift_plan,
    get_shift_plan,
)
from article_factory.services.shift_windows import shift_window_containing

logger = logging.getLogger(__name__)

DESK_STALL_MINUTES = 60


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def auto_scheduler_enabled(db: Session) -> bool:
    row = get_or_create_factory_settings(db)
    return bool(getattr(row, "auto_scheduler_enabled", True))


def find_active_plans_past_end(db: Session, *, now: datetime | None = None) -> list[ShiftPlan]:
    current = _utc(now or datetime.now(timezone.utc))
    return (
        db.query(ShiftPlan)
        .filter(ShiftPlan.status == "active", ShiftPlan.window_ends_at <= current)
        .order_by(ShiftPlan.window_ends_at.asc())
        .all()
    )


def find_draft_plans_due_for_activation(db: Session, *, now: datetime | None = None) -> list[ShiftPlan]:
    current = _utc(now or datetime.now(timezone.utc))
    return (
        db.query(ShiftPlan)
        .filter(
            ShiftPlan.status == "draft",
            ShiftPlan.window_starts_at <= current,
            ShiftPlan.window_ends_at > current,
        )
        .order_by(ShiftPlan.window_starts_at.asc())
        .all()
    )


def bootstrap_desks_from_standing_orders(db: Session, plan: ShiftPlan) -> int:
    if plan.status != "draft":
        return 0
    existing = {
        row.desk_path
        for row in db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).all()
    }
    orders = db.query(StandingOrder).filter_by(shift_key=plan.shift_key).order_by(StandingOrder.desk_path).all()
    created = 0
    for order in orders:
        if order.desk_path in existing:
            continue
        add_desk_slot(db, plan_id=plan.id, desk_path=order.desk_path)
        existing.add(order.desk_path)
        created += 1
    return created


def apply_standing_orders_only(db: Session, plan: ShiftPlan) -> int:
    desks = (
        db.query(ShiftDeskSlot)
        .filter_by(shift_plan_id=plan.id)
        .order_by(ShiftDeskSlot.dispatch_order, ShiftDeskSlot.id)
        .all()
    )
    total = 0
    for slot in desks:
        standing = get_standing_order(db, desk_path=slot.desk_path, shift_key=plan.shift_key)
        total += apply_standing_to_desk(db, slot=slot, standing=standing)
    return total


async def fill_roster_at_boundary(
    db: Session,
    plan: ShiftPlan,
    *,
    cp: ControlPlaneClient | None,
    puller: str | None,
) -> dict[str, Any]:
    """Apply standing orders and run T-15 AI fill when possible."""
    bootstrap_desks_from_standing_orders(db, plan)
    standing_total = apply_standing_orders_only(db, plan)
    summary: dict[str, Any] = {"standing_total": standing_total, "t15": None}

    counts = _assignment_counts(db, plan.id)
    if sum(counts.values()) > 0 and plan.t15_applied_at is not None:
        return summary

    if cp is None or not puller:
        if sum(_assignment_counts(db, plan.id).values()) < 1:
            create_alert(
                db,
                kind="roster_incomplete_at_start",
                severity="warning",
                message=f"Could not fill roster for {plan.shift_key} shift — control plane or puller unavailable.",
                shift_plan_id=plan.id,
                dedupe_key=f"roster-fill:{plan.id}",
            )
        return summary

    try:
        summary["t15"] = await run_t15_for_plan(db, plan_id=plan.id, cp=cp, puller=puller)
    except ValueError as exc:
        logger.warning("Boundary roster fill skipped for plan %s: %s", plan.id, exc)
    return summary


async def hard_stop_shift_plan(
    db: Session,
    plan_id: int,
    *,
    reason: str = "Shift window ended",
) -> ShiftPlan:
    from article_factory.orchestrator.runner import factory_loop
    from article_factory.services.run_control import clear_run_cancel, request_run_cancel, reassert_runs_stopped

    plan = get_shift_plan(db, plan_id)
    if plan.status != "active":
        return plan

    slot_ids = [row.id for row in db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id).all()]
    assignment_ids: list[int] = []
    if slot_ids:
        assignment_ids = [
            row.id
            for row in db.query(ShiftAssignment.id)
            .filter(ShiftAssignment.shift_desk_slot_id.in_(slot_ids))
            .all()
        ]

    runs: list[FactoryRun] = []
    if assignment_ids:
        runs = (
            db.query(FactoryRun)
            .filter(
                FactoryRun.shift_assignment_id.in_(assignment_ids),
                FactoryRun.status == "running",
            )
            .all()
        )

    run_ids = [run.run_id for run in runs]
    queue_item_ids = [run.queue_item_id for run in runs if run.queue_item_id]

    for run_id in run_ids:
        await request_run_cancel(run_id)

    reassert_runs_stopped(db, run_ids, error=reason)
    if run_ids or queue_item_ids:
        factory_loop.cancel_run_workers(run_ids=run_ids, queue_item_ids=queue_item_ids)

    now = datetime.now(timezone.utc)
    if slot_ids:
        for assignment in (
            db.query(ShiftAssignment)
            .filter(
                ShiftAssignment.shift_desk_slot_id.in_(slot_ids),
                ShiftAssignment.status.in_(("pending", "running")),
            )
            .all()
        ):
            assignment.status = "failed"
            assignment.finished_at = now

    plan.status = "complete"
    plan.completed_at = now
    db.flush()

    for run_id in run_ids:
        await clear_run_cancel(run_id)

    create_alert(
        db,
        kind="shift_hard_stopped",
        severity="info",
        message=f"{plan.shift_key.title()} shift ended — in-flight work cancelled at window boundary.",
        shift_plan_id=plan.id,
        dedupe_key=f"hard-stop:{plan.id}",
    )
    resolve_alerts_for_plan(db, shift_plan_id=plan.id, kinds={"desk_stalled"})
    return plan


async def try_auto_activate_plan(
    db: Session,
    plan: ShiftPlan,
    *,
    cp: ControlPlaneClient | None,
    puller: str | None,
) -> bool:
    if plan.status != "draft":
        return False

    bootstrap_desks_from_standing_orders(db, plan)
    desk_count = db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id).count()
    if desk_count < 1:
        create_alert(
            db,
            kind="shift_started_empty",
            severity="warning",
            message=f"{plan.shift_key.title()} shift started with no staffed desks — add standing orders or plan the shift.",
            shift_plan_id=plan.id,
            dedupe_key=f"empty-shift:{plan.id}",
        )
        return False

    counts = _assignment_counts(db, plan.id)
    if sum(counts.values()) < 1:
        await fill_roster_at_boundary(db, plan, cp=cp, puller=puller)

    counts = _assignment_counts(db, plan.id)
    if sum(counts.values()) < 1:
        create_alert(
            db,
            kind="shift_started_empty",
            severity="warning",
            message=f"{plan.shift_key.title()} shift started but no assignments could be loaded.",
            shift_plan_id=plan.id,
            dedupe_key=f"empty-shift:{plan.id}",
        )
        return False

    active_other = db.query(ShiftPlan).filter_by(status="active").filter(ShiftPlan.id != plan.id).first()
    if active_other is not None:
        await hard_stop_shift_plan(db, active_other.id, reason="Superseded by next shift")

    activate_shift_plan(db, plan.id)
    resolve_alerts_for_plan(
        db,
        shift_plan_id=plan.id,
        kinds={"shift_started_empty", "roster_incomplete_at_start"},
    )
    create_alert(
        db,
        kind="shift_auto_activated",
        severity="info",
        message=f"{plan.shift_key.title()} shift auto-activated with {sum(_assignment_counts(db, plan.id).values())} assignment(s).",
        shift_plan_id=plan.id,
        dedupe_key=f"auto-activate:{plan.id}",
    )
    return True


def check_desk_stalls(db: Session, *, now: datetime | None = None) -> int:
    current = _utc(now or datetime.now(timezone.utc))
    cutoff = current - timedelta(minutes=DESK_STALL_MINUTES)
    active = db.query(ShiftPlan).filter_by(status="active").order_by(ShiftPlan.activated_at.desc()).first()
    if active is None:
        return 0

    stalled = (
        db.query(ShiftAssignment)
        .join(ShiftDeskSlot, ShiftAssignment.shift_desk_slot_id == ShiftDeskSlot.id)
        .filter(
            ShiftDeskSlot.shift_plan_id == active.id,
            ShiftAssignment.status == "running",
            ShiftAssignment.dispatched_at.isnot(None),
            ShiftAssignment.dispatched_at <= cutoff,
        )
        .count()
    )
    if stalled < 1:
        return 0

    create_alert(
        db,
        kind="desk_stalled",
        severity="warning",
        message=f"{stalled} assignment(s) have been running for over {DESK_STALL_MINUTES} minutes on the active shift.",
        shift_plan_id=active.id,
        dedupe_key=f"desk-stall:{active.id}",
    )
    return 1


def check_t15_roster_gaps(db: Session, *, now: datetime | None = None) -> int:
    current = _utc(now or datetime.now(timezone.utc))
    window_end = current + timedelta(minutes=T15_LEAD_MINUTES)
    plans = (
        db.query(ShiftPlan)
        .filter(
            ShiftPlan.status == "draft",
            ShiftPlan.window_starts_at > current,
            ShiftPlan.window_starts_at <= window_end,
            ShiftPlan.t15_applied_at.is_(None),
        )
        .all()
    )
    created = 0
    for plan in plans:
        desk_count = db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id).count()
        if desk_count < 1:
            bootstrap_desks_from_standing_orders(db, plan)
            desk_count = db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id).count()
        if desk_count < 1:
            create_alert(
                db,
                kind="roster_incomplete_t15",
                severity="warning",
                message=f"Roster incomplete for upcoming {plan.shift_key} shift — no desks staffed before T-15.",
                shift_plan_id=plan.id,
                dedupe_key=f"t15-empty:{plan.id}",
            )
            created += 1
    return created


async def process_shift_boundaries(
    db: Session,
    *,
    pullers: list[dict] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Run one scheduler tick: end overdue shifts, auto-activate due drafts, check alerts."""
    if not auto_scheduler_enabled(db):
        return {"ended": 0, "activated": 0, "alerts": 0}

    summary = {"ended": 0, "activated": 0, "alerts": 0}
    current = _utc(now or datetime.now(timezone.utc))

    for plan in find_active_plans_past_end(db, now=current):
        await hard_stop_shift_plan(db, plan.id)
        summary["ended"] += 1

    runtime = load_runtime_settings(db)
    cp: ControlPlaneClient | None = None
    puller_name = ""
    cp_url = (runtime.control_plane_url or "").strip()
    if cp_url:
        cp = ControlPlaneClient(base_url=cp_url)
        if pullers is None:
            try:
                pullers = await cp.list_pullers(active_only=False)
            except Exception:
                pullers = []
        model = (runtime.default_model or "").strip()
        if model and pullers:
            idle = idle_pullers_for_model(pullers, model)
            if idle:
                puller_name = str(idle[0].get("puller_name") or "")

    window = shift_window_containing(current)
    get_or_create_shift_plan(db, window)

    for plan in find_draft_plans_due_for_activation(db, now=current):
        if await try_auto_activate_plan(db, plan, cp=cp, puller=puller_name or None):
            summary["activated"] += 1

    summary["alerts"] += check_desk_stalls(db, now=current)
    summary["alerts"] += check_t15_roster_gaps(db, now=current)
    return summary
