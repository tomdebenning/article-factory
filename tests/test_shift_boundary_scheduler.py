from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import article_factory.db as db_module
from article_factory.models import FactoryRun, ShiftAssignment, ShiftDeskSlot, ShiftPlan, StandingOrder
from article_factory.services.newsroom_alerts import list_active_alerts
from article_factory.services.shift_boundary_scheduler import (
    bootstrap_desks_from_standing_orders,
    find_active_plans_past_end,
    hard_stop_shift_plan,
    process_shift_boundaries,
)
from article_factory.services.shift_plans import add_desk_slot, get_or_create_shift_plan, replace_desk_assignments
from article_factory.services.shift_windows import shift_window_containing


@pytest.mark.asyncio
async def test_hard_stop_cancels_running_assignments(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        window = shift_window_containing(now)
        plan = get_or_create_shift_plan(db, window)
        slot = add_desk_slot(db, plan_id=plan.id, desk_path="sports/standard-4-step.flow.json")
        replace_desk_assignments(db, desk_slot_id=slot.id, prompts=["Story one"])
        plan.status = "active"
        plan.activated_at = now - timedelta(hours=1)
        plan.window_ends_at = now - timedelta(minutes=1)
        assignment = db.query(ShiftAssignment).filter_by(shift_desk_slot_id=slot.id).one()
        assignment.status = "running"
        run = FactoryRun(
            run_id="run-boundary-stop",
            topic_slug="sports",
            status="running",
            shift_assignment_id=assignment.id,
        )
        db.add(run)
        db.commit()

        stopped = await hard_stop_shift_plan(db, plan.id, reason="Test stop")
        db.commit()
        assert stopped.status == "complete"
        db.refresh(assignment)
        assert assignment.status == "failed"
        db.refresh(run)
        assert run.status == "cancelled"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_process_shift_boundaries_auto_activate_with_standing_orders(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        now = datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc)
        window = shift_window_containing(now)
        plan = get_or_create_shift_plan(db, window)
        plan.status = "draft"
        plan.default_model = "test-model"
        db.add(
            StandingOrder(
                desk_path="sports/standard-4-step.flow.json",
                shift_key=plan.shift_key,
                topics=["Local team wins", "Coach interview"],
            )
        )
        db.commit()

        async def fake_t15(db, *, plan_id, cp, puller):
            slot = add_desk_slot(db, plan_id=plan_id, desk_path="sports/standard-4-step.flow.json")
            replace_desk_assignments(db, desk_slot_id=slot.id, prompts=["Standing story"], source="standing")
            plan_row = db.get(ShiftPlan, plan_id)
            plan_row.t15_applied_at = datetime.now(timezone.utc)
            return {"plan_id": plan_id}

        monkeypatch.setattr(
            "article_factory.services.shift_boundary_scheduler.run_t15_for_plan",
            fake_t15,
        )

        summary = await process_shift_boundaries(db, pullers=[{"puller_name": "gpu-01", "supported_models": ["test-model"]}], now=now)
        db.commit()
        db.refresh(plan)
        assert summary["activated"] == 1
        assert plan.status == "active"
    finally:
        db.close()


def test_bootstrap_desks_from_standing_orders(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        now = datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc)
        plan = get_or_create_shift_plan(db, shift_window_containing(now))
        db.add(
            StandingOrder(
                desk_path="sports/standard-4-step.flow.json",
                shift_key=plan.shift_key,
                topics=["Topic A"],
            )
        )
        db.commit()
        created = bootstrap_desks_from_standing_orders(db, plan)
        assert created == 1
        assert db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_empty_shift_creates_alert(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        now = datetime(2026, 7, 17, 13, 0, tzinfo=timezone.utc)
        window = shift_window_containing(now)
        plan = get_or_create_shift_plan(db, window)
        plan.status = "draft"
        db.commit()

        summary = await process_shift_boundaries(db, pullers=[], now=now)
        db.commit()
        assert summary["activated"] == 0
        alerts = list_active_alerts(db)
        assert any(alert.kind == "shift_started_empty" for alert in alerts)
    finally:
        db.close()


def test_find_active_plans_past_end(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        now = datetime(2026, 7, 17, 18, 30, tzinfo=timezone.utc)
        window = shift_window_containing(now - timedelta(hours=7))
        plan = get_or_create_shift_plan(db, window)
        plan.status = "active"
        plan.window_ends_at = now - timedelta(minutes=5)
        db.commit()
        due = find_active_plans_past_end(db, now=now)
        assert any(row.id == plan.id for row in due)
    finally:
        db.close()
