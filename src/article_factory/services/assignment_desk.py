"""Assignment Desk — standing orders and AI roster suggestions (Phase 4)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import ShiftAssignment, ShiftDeskSlot, ShiftPlan, StandingOrder
from article_factory.services.control_plane_completion import extract_json_object, run_control_plane_completion
from article_factory.services.flow_versions import resolve_flow_for_run
from article_factory.models import FactoryRun
from article_factory.services.puller_selection import idle_pullers_for_model
from article_factory.services.runtime_settings import load_runtime_settings
from article_factory.services.shift_plans import get_shift_plan
from article_factory.services.shift_windows import SHIFT_LABELS

logger = logging.getLogger(__name__)

T15_LEAD_MINUTES = 15
ASSIGNMENT_SOURCES = frozenset({"standing", "ai_suggested", "manual"})


def load_desk_brief(db: Session, *, desk_path: str, flow_version_id: int | None) -> str:
    preview = FactoryRun(flow_path=desk_path, flow_version_id=flow_version_id)
    try:
        flow = resolve_flow_for_run(db, preview)
    except Exception:
        try:
            from article_factory.services.flow_storage import read_flow

            flow = read_flow(desk_path)
        except Exception:
            return ""
    return (getattr(flow, "beat_brief", None) or "").strip()


def get_standing_order(db: Session, *, desk_path: str, shift_key: str) -> StandingOrder | None:
    return (
        db.query(StandingOrder)
        .filter_by(desk_path=desk_path.strip(), shift_key=shift_key.strip())
        .one_or_none()
    )


def standing_order_payload(order: StandingOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "desk_path": order.desk_path,
        "shift_key": order.shift_key,
        "topics": list(order.topics or []),
        "target_count": order.target_count,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


def upsert_standing_order(
    db: Session,
    *,
    desk_path: str,
    shift_key: str,
    topics: list[str],
    target_count: int | None,
) -> StandingOrder:
    cleaned_path = desk_path.strip()
    cleaned_key = shift_key.strip().lower()
    cleaned_topics = [line.strip() for line in topics if line.strip()]
    order = get_standing_order(db, desk_path=cleaned_path, shift_key=cleaned_key)
    if order is None:
        order = StandingOrder(desk_path=cleaned_path, shift_key=cleaned_key)
        db.add(order)
    order.topics = cleaned_topics
    order.target_count = target_count
    db.flush()
    return order


def list_standing_orders_for_desk(db: Session, *, desk_path: str) -> list[StandingOrder]:
    return (
        db.query(StandingOrder)
        .filter_by(desk_path=desk_path.strip())
        .order_by(StandingOrder.shift_key)
        .all()
    )


def _effective_target(standing: StandingOrder | None) -> int:
    if standing is None:
        return 0
    topics = [line.strip() for line in (standing.topics or []) if line.strip()]
    if standing.target_count is not None:
        return max(0, int(standing.target_count))
    return len(topics)


def _next_priority(db: Session, slot_id: int) -> int:
    row = (
        db.query(ShiftAssignment.priority)
        .filter_by(shift_desk_slot_id=slot_id)
        .order_by(ShiftAssignment.priority.desc())
        .first()
    )
    return (row[0] + 10) if row else 100


def _count_assignments_toward_target(db: Session, slot_id: int) -> int:
    return (
        db.query(ShiftAssignment)
        .filter_by(shift_desk_slot_id=slot_id)
        .filter(ShiftAssignment.status.in_(("pending", "running")))
        .count()
    )


def apply_standing_to_desk(
    db: Session,
    *,
    slot: ShiftDeskSlot,
    standing: StandingOrder | None,
) -> int:
    """Replace pending standing assignments; preserve locked manual lines."""
    db.query(ShiftAssignment).filter(
        ShiftAssignment.shift_desk_slot_id == slot.id,
        ShiftAssignment.source == "standing",
        ShiftAssignment.status == "pending",
    ).delete(synchronize_session=False)

    if standing is None:
        db.flush()
        return 0

    topics = [line.strip() for line in (standing.topics or []) if line.strip()]
    target = _effective_target(standing)
    if target <= 0:
        db.flush()
        return 0

    locked_filled = (
        db.query(ShiftAssignment)
        .filter_by(shift_desk_slot_id=slot.id, locked=True)
        .filter(ShiftAssignment.status.in_(("pending", "running")))
        .count()
    )
    slots_for_standing = max(0, min(len(topics), target - locked_filled))
    priority = _next_priority(db, slot.id)
    created = 0
    for index, prompt in enumerate(topics[:slots_for_standing]):
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt=prompt,
                priority=priority + index,
                status="pending",
                source="standing",
                locked=False,
            )
        )
        created += 1
    db.flush()
    return created


def clear_pending_ai_suggestions(db: Session, *, slot_id: int) -> int:
    deleted = (
        db.query(ShiftAssignment)
        .filter_by(shift_desk_slot_id=slot_id, source="ai_suggested", status="pending")
        .delete(synchronize_session=False)
    )
    db.flush()
    return deleted


def ai_gap_for_desk(db: Session, *, slot: ShiftDeskSlot, standing: StandingOrder | None) -> int:
    target = _effective_target(standing)
    if target <= 0:
        return 0
    filled = _count_assignments_toward_target(db, slot.id)
    return max(0, target - filled)


def _build_suggestion_messages(
    *,
    desk_name: str,
    desk_path: str,
    beat_brief: str,
    shift_key: str,
    count: int,
) -> list[dict[str, str]]:
    shift_label = SHIFT_LABELS.get(shift_key, shift_key.title())  # type: ignore[arg-type]
    system = (
        "You are the Assignment Desk editor for an AI newsroom. "
        "Propose concise story assignment prompts (one line each) for a desk to cover during an upcoming shift. "
        "Return JSON only: {\"assignments\": [\"prompt one\", \"prompt two\", ...]}"
    )
    user = (
        f"Shift: {shift_label}\n"
        f"Desk: {desk_name or desk_path}\n"
        f"Beat brief:\n{beat_brief or '(No brief provided — infer from desk name.)'}\n\n"
        f"Propose exactly {count} distinct assignment prompt(s). Each should be a specific, actionable story angle."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def suggest_ai_assignments_for_desk(
    db: Session,
    *,
    slot: ShiftDeskSlot,
    plan: ShiftPlan,
    count: int,
    cp: ControlPlaneClient,
    puller: str,
) -> list[str]:
    if count <= 0:
        return []

    model = (plan.default_model or "").strip()
    if not model:
        runtime = load_runtime_settings(db)
        model = (runtime.default_model or "").strip()
    if not model:
        logger.warning("Skipping AI suggestions — no model configured for shift plan %s", plan.id)
        return []

    beat = load_desk_brief(db, desk_path=slot.desk_path, flow_version_id=slot.flow_version_id)
    messages = _build_suggestion_messages(
        desk_name=slot.name,
        desk_path=slot.desk_path,
        beat_brief=beat,
        shift_key=plan.shift_key,
        count=count,
    )
    try:
        raw = await run_control_plane_completion(
            cp=cp,
            puller=puller,
            model=model,
            messages=messages,
            agent_id="factory-assignment-desk",
        )
        payload = extract_json_object(raw)
    except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
        logger.warning("AI assignment suggestion failed for desk slot %s: %s", slot.id, exc)
        return []

    prompts: list[str] = []
    for item in payload.get("assignments") or []:
        line = str(item).strip()
        if line:
            prompts.append(line)
        if len(prompts) >= count:
            break
    return prompts[:count]


def add_ai_suggestions(db: Session, *, slot: ShiftDeskSlot, prompts: list[str]) -> int:
    if not prompts:
        return 0
    priority = _next_priority(db, slot.id)
    created = 0
    for index, prompt in enumerate(prompts):
        cleaned = prompt.strip()
        if not cleaned:
            continue
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt=cleaned,
                priority=priority + index,
                status="pending",
                source="ai_suggested",
                locked=False,
            )
        )
        created += 1
    db.flush()
    return created


async def run_t15_for_plan(
    db: Session,
    *,
    plan_id: int,
    cp: ControlPlaneClient,
    puller: str,
) -> dict[str, Any]:
    plan = get_shift_plan(db, plan_id)
    if plan.status != "draft":
        raise ValueError("T-15 roster generation only applies to draft shifts")
    if plan.t15_applied_at is not None:
        return {"plan_id": plan.id, "skipped": True, "reason": "already_applied"}

    desks = (
        db.query(ShiftDeskSlot)
        .filter_by(shift_plan_id=plan.id)
        .order_by(ShiftDeskSlot.dispatch_order, ShiftDeskSlot.id)
        .all()
    )
    summary: dict[str, Any] = {
        "plan_id": plan.id,
        "desks": [],
        "standing_total": 0,
        "ai_total": 0,
    }

    for slot in desks:
        standing = get_standing_order(db, desk_path=slot.desk_path, shift_key=plan.shift_key)
        clear_pending_ai_suggestions(db, slot_id=slot.id)
        standing_count = apply_standing_to_desk(db, slot=slot, standing=standing)
        gap = ai_gap_for_desk(db, slot=slot, standing=standing)
        ai_prompts: list[str] = []
        if gap > 0:
            ai_prompts = await suggest_ai_assignments_for_desk(
                db,
                slot=slot,
                plan=plan,
                count=gap,
                cp=cp,
                puller=puller,
            )
        ai_count = add_ai_suggestions(db, slot=slot, prompts=ai_prompts)
        summary["standing_total"] += standing_count
        summary["ai_total"] += ai_count
        summary["desks"].append(
            {
                "desk_slot_id": slot.id,
                "desk_path": slot.desk_path,
                "standing": standing_count,
                "ai_suggested": ai_count,
            }
        )

    now = datetime.now(timezone.utc)
    plan.t15_applied_at = now
    plan.roster_generated_at = now
    plan.roster_review_status = "pending"
    db.flush()
    return summary


def approve_roster(db: Session, *, plan_id: int) -> ShiftPlan:
    plan = get_shift_plan(db, plan_id)
    if plan.roster_review_status not in {"pending", "ready"}:
        if plan.t15_applied_at is None:
            raise ValueError("No generated roster to approve")
    plan.roster_review_status = "ready"
    db.flush()
    return plan


def reject_ai_suggestions(db: Session, *, plan_id: int) -> int:
    plan = get_shift_plan(db, plan_id)
    slot_ids = [
        row.id
        for row in db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id).all()
    ]
    if not slot_ids:
        return 0
    deleted = (
        db.query(ShiftAssignment)
        .filter(
            ShiftAssignment.shift_desk_slot_id.in_(slot_ids),
            ShiftAssignment.source == "ai_suggested",
            ShiftAssignment.status == "pending",
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return deleted


def update_roster_assignments(
    db: Session,
    *,
    plan_id: int,
    updates: list[dict[str, Any]],
) -> None:
    plan = get_shift_plan(db, plan_id)
    if plan.status != "draft":
        raise ValueError("Roster can only be edited while the shift is a draft")
    slot_ids = {row.id for row in db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).all()}
    for item in updates:
        assignment_id = item.get("id")
        if assignment_id is None:
            continue
        row = db.get(ShiftAssignment, int(assignment_id))
        if row is None or row.shift_desk_slot_id not in slot_ids:
            continue
        if row.status != "pending":
            continue
        if "prompt" in item:
            prompt = str(item["prompt"] or "").strip()
            if prompt:
                row.prompt = prompt
        if "locked" in item:
            row.locked = bool(item["locked"])
        if row.source == "ai_suggested" and item.get("promote_to_manual"):
            row.source = "manual"
    db.flush()


def assignment_is_dispatchable(assignment: ShiftAssignment, plan: ShiftPlan) -> bool:
    if plan.roster_review_status == "ready" or plan.roster_review_status == "none":
        return True
    if plan.roster_review_status == "pending":
        return assignment.source != "ai_suggested"
    return True
