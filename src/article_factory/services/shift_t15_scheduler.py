"""Poll for shift plans due for T-15 roster generation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import ShiftDeskSlot, ShiftPlan
from article_factory.services.assignment_desk import T15_LEAD_MINUTES, run_t15_for_plan
from article_factory.services.puller_selection import idle_pullers_for_model
from article_factory.services.runtime_settings import load_runtime_settings

logger = logging.getLogger(__name__)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def find_plans_due_for_t15(db: Session, *, now: datetime | None = None) -> list[ShiftPlan]:
    """Return draft plans whose shift starts within T-15 window and have staffed desks."""
    current = _utc(now or datetime.now(timezone.utc))
    window_end = current + timedelta(minutes=T15_LEAD_MINUTES)

    candidates = (
        db.query(ShiftPlan)
        .filter(
            ShiftPlan.status == "draft",
            ShiftPlan.t15_applied_at.is_(None),
            ShiftPlan.window_starts_at > current,
            ShiftPlan.window_starts_at <= window_end,
        )
        .order_by(ShiftPlan.window_starts_at.asc())
        .all()
    )
    due: list[ShiftPlan] = []
    for plan in candidates:
        desk_count = db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id).count()
        if desk_count > 0:
            due.append(plan)
    return due


async def process_t15_due_plans(db: Session, *, pullers: list[dict] | None = None) -> int:
    """Run T-15 roster generation for all due plans. Returns count processed."""
    plans = find_plans_due_for_t15(db)
    if not plans:
        return 0

    runtime = load_runtime_settings(db)
    cp_url = (runtime.control_plane_url or "").strip()
    if not cp_url:
        logger.debug("T-15 check skipped — control plane URL not configured")
        return 0

    cp = ControlPlaneClient(base_url=cp_url)
    if pullers is None:
        try:
            pullers = await cp.list_pullers(active_only=False)
        except Exception:
            logger.warning("T-15 check could not list pullers")
            return 0

    processed = 0
    for plan in plans:
        model = (plan.default_model or runtime.default_model or "").strip()
        if not model:
            logger.warning("T-15 skipped plan %s — no model configured", plan.id)
            continue
        idle = idle_pullers_for_model(pullers, model)
        if not idle:
            logger.warning("T-15 skipped plan %s — no idle puller for model %s", plan.id, model)
            continue
        puller_name = str(idle[0].get("puller_name") or "")
        if not puller_name:
            continue
        try:
            await run_t15_for_plan(db, plan_id=plan.id, cp=cp, puller=puller_name)
            processed += 1
            logger.info("T-15 roster generated for shift plan %s", plan.id)
        except Exception:
            logger.exception("T-15 roster generation failed for plan %s", plan.id)
    return processed
