from datetime import datetime, timedelta, timezone

import pytest

from article_factory.models import ShiftAssignment, ShiftDeskSlot, ShiftPlan
from article_factory.services.assignment_desk import (
    apply_standing_to_desk,
    approve_roster,
    assignment_is_dispatchable,
    reject_ai_suggestions,
    run_t15_for_plan,
    upsert_standing_order,
)
from article_factory.services.shift_dispatch import select_pending_assignments_round_robin
from article_factory.services.shift_t15_scheduler import find_plans_due_for_t15


def _draft_plan(db, *, starts_in_minutes: int = 20) -> ShiftPlan:
    now = datetime.now(timezone.utc)
    starts = now + timedelta(minutes=starts_in_minutes)
    plan = ShiftPlan(
        shift_key="morning",
        window_starts_at=starts,
        window_ends_at=starts + timedelta(hours=6),
        status="draft",
        default_model="test-model",
    )
    db.add(plan)
    db.flush()
    slot = ShiftDeskSlot(
        shift_plan_id=plan.id,
        name="Sports",
        desk_path="test/debug-verdict.flow.json",
        topic_slug="general",
    )
    db.add(slot)
    db.flush()
    return plan


def test_standing_order_apply_replaces_standing_preserves_locked(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()

        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="Locked manual",
                source="manual",
                locked=True,
                status="pending",
            )
        )
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="Old standing",
                source="standing",
                status="pending",
            )
        )
        db.flush()

        standing = upsert_standing_order(
            db,
            desk_path=slot.desk_path,
            shift_key=plan.shift_key,
            topics=["Topic A", "Topic B"],
            target_count=4,
        )
        apply_standing_to_desk(db, slot=slot, standing=standing)
        db.commit()

        rows = db.query(ShiftAssignment).filter_by(shift_desk_slot_id=slot.id).all()
        prompts = {row.prompt: row for row in rows}
        assert "Locked manual" in prompts
        assert prompts["Locked manual"].locked is True
        assert "Old standing" not in prompts
        assert "Topic A" in prompts
        assert "Topic B" in prompts
    finally:
        db.close()


def test_ai_gap_and_dispatch_filter_pending_review(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()

        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="Standing line",
                source="standing",
                status="pending",
            )
        )
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="AI line",
                source="ai_suggested",
                status="pending",
            )
        )
        plan.roster_review_status = "pending"
        db.commit()

        standing_row = db.query(ShiftAssignment).filter_by(source="standing").one()
        ai_row = db.query(ShiftAssignment).filter_by(source="ai_suggested").one()
        assert assignment_is_dispatchable(standing_row, plan) is True
        assert assignment_is_dispatchable(ai_row, plan) is False

        plan.status = "active"
        plan.activated_at = datetime.now(timezone.utc)
        db.commit()

        picked, _ = select_pending_assignments_round_robin(db, limit=5)
        picked_ids = {row.id for row, _, _ in picked}
        assert standing_row.id in picked_ids
        assert ai_row.id not in picked_ids
    finally:
        db.close()


def test_approve_roster_allows_ai_dispatch(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="AI line",
                source="ai_suggested",
                status="pending",
            )
        )
        plan.roster_review_status = "pending"
        plan.t15_applied_at = datetime.now(timezone.utc)
        db.commit()

        approve_roster(db, plan_id=plan.id)
        db.commit()
        db.refresh(plan)
        assert plan.roster_review_status == "ready"

        ai_row = db.query(ShiftAssignment).filter_by(source="ai_suggested").one()
        assert assignment_is_dispatchable(ai_row, plan) is True
    finally:
        db.close()


def test_reject_ai_suggestions(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="AI line",
                source="ai_suggested",
                status="pending",
            )
        )
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="Standing line",
                source="standing",
                status="pending",
            )
        )
        db.commit()

        removed = reject_ai_suggestions(db, plan_id=plan.id)
        db.commit()
        assert removed == 1
        remaining = db.query(ShiftAssignment).filter_by(shift_desk_slot_id=slot.id).all()
        assert len(remaining) == 1
        assert remaining[0].source == "standing"
    finally:
        db.close()


def test_find_plans_due_for_t15(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        due_plan = _draft_plan(db, starts_in_minutes=10)
        far_plan = _draft_plan(db, starts_in_minutes=120)
        far_plan.window_starts_at = datetime.now(timezone.utc) + timedelta(hours=2)
        db.commit()

        now = datetime.now(timezone.utc)
        due = find_plans_due_for_t15(db, now=now)
        due_ids = {plan.id for plan in due}
        assert due_plan.id in due_ids
        assert far_plan.id not in due_ids
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_t15_for_plan_with_mock_ai(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db, starts_in_minutes=10)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        upsert_standing_order(
            db,
            desk_path=slot.desk_path,
            shift_key=plan.shift_key,
            topics=["Standing one"],
            target_count=2,
        )
        db.commit()

        async def fake_suggest(*args, **kwargs):
            return ["AI suggested topic"]

        monkeypatch.setattr(
            "article_factory.services.assignment_desk.suggest_ai_assignments_for_desk",
            fake_suggest,
        )

        class FakeCp:
            pass

        summary = await run_t15_for_plan(db, plan_id=plan.id, cp=FakeCp(), puller="gpu-01")
        db.commit()
        assert summary["standing_total"] == 1
        assert summary["ai_total"] == 1
        db.refresh(plan)
        assert plan.roster_review_status == "pending"
        assert plan.t15_applied_at is not None

        rows = db.query(ShiftAssignment).filter_by(shift_desk_slot_id=slot.id).all()
        sources = {row.source for row in rows}
        assert sources == {"standing", "ai_suggested"}
    finally:
        db.close()


def test_standing_orders_api(client, api_headers, configured_db) -> None:
    put = client.put(
        "/api/standing-orders",
        headers=api_headers,
        json={
            "desk_path": "test/debug-verdict.flow.json",
            "shift_key": "morning",
            "topics": ["Daily recap", "Player spotlight"],
            "target_count": 3,
        },
    )
    assert put.status_code == 200, put.text

    listed = client.get(
        "/api/standing-orders",
        headers=api_headers,
        params={"desk_path": "test/debug-verdict.flow.json"},
    )
    assert listed.status_code == 200
    morning = next(row for row in listed.json()["shifts"] if row["shift_key"] == "morning")
    assert morning["topics"] == ["Daily recap", "Player spotlight"]
    assert morning["target_count"] == 3


def test_roster_review_api(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        assignment = ShiftAssignment(
            shift_desk_slot_id=slot.id,
            prompt="AI topic",
            source="ai_suggested",
            status="pending",
        )
        db.add(assignment)
        plan.roster_review_status = "pending"
        plan.t15_applied_at = datetime.now(timezone.utc)
        db.commit()
        plan_id = plan.id
    finally:
        db.close()

    reject = client.post(f"/api/shifts/plans/{plan_id}/roster/reject-ai", headers=api_headers)
    assert reject.status_code == 200
    assert reject.json()["removed"] == 1

    db = SessionLocal()
    try:
        plan = db.get(ShiftPlan, plan_id)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt="Standing topic",
                source="standing",
                status="pending",
            )
        )
        plan.roster_review_status = "pending"
        db.commit()
    finally:
        db.close()

    approve = client.post(f"/api/shifts/plans/{plan_id}/roster/approve", headers=api_headers)
    assert approve.status_code == 200
    assert approve.json()["plan"]["roster_review_status"] == "ready"


def test_standing_orders_api_validation(client, api_headers) -> None:
    bad_shift = client.put(
        "/api/standing-orders",
        headers=api_headers,
        json={
            "desk_path": "test/debug-verdict.flow.json",
            "shift_key": "invalid",
            "topics": [],
            "target_count": 1,
        },
    )
    assert bad_shift.status_code == 400

    bad_target = client.put(
        "/api/standing-orders",
        headers=api_headers,
        json={
            "desk_path": "test/debug-verdict.flow.json",
            "shift_key": "morning",
            "topics": [],
            "target_count": -1,
        },
    )
    assert bad_target.status_code == 400


def test_shift_save_desks_only_t15_message(client, api_headers, configured_db) -> None:
    from article_factory.services.shift_windows import today_and_tomorrow_shift_windows

    window = today_and_tomorrow_shift_windows()[2]
    save = client.post(
        "/api/shifts/plans/save",
        headers=api_headers,
        json={
            "window_key": window.window_key,
            "default_model": "test-model",
            "desks": [
                {
                    "desk_path": "test/debug-verdict.flow.json",
                    "topic_slug": "general",
                    "name": "Sports",
                }
            ],
            "assignments_by_desk_index": {},
        },
    )
    assert save.status_code == 200, save.text
    assert "T-15" in save.json()["message"]
    assert save.json()["plan"]["assignment_total"] == 0


def test_get_shift_plan_and_ensure(client, api_headers, configured_db) -> None:
    from article_factory.services.shift_windows import today_and_tomorrow_shift_windows

    window = today_and_tomorrow_shift_windows()[3]
    ensured = client.post(
        "/api/shifts/plans/ensure",
        headers=api_headers,
        json={"window_key": window.window_key},
    )
    assert ensured.status_code == 200
    plan_id = ensured.json()["plan"]["id"]

    fetched = client.get(f"/api/shifts/plans/{plan_id}", headers=api_headers)
    assert fetched.status_code == 200
    assert fetched.json()["plan"]["id"] == plan_id

    missing = client.get("/api/shifts/plans/999999", headers=api_headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_run_t15_skips_already_applied(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        plan.t15_applied_at = datetime.now(timezone.utc)
        db.commit()
        summary = await run_t15_for_plan(db, plan_id=plan.id, cp=object(), puller="p1")
        assert summary.get("skipped") is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_t15_rejects_active_plan(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        plan.status = "active"
        db.commit()

        with pytest.raises(ValueError, match="draft"):
            await run_t15_for_plan(db, plan_id=plan.id, cp=object(), puller="p1")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_suggest_ai_handles_llm_failure(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.assignment_desk import suggest_ai_assignments_for_desk

    async def boom(*args, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(
        "article_factory.services.assignment_desk.run_control_plane_completion",
        boom,
    )

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        db.commit()
        prompts = await suggest_ai_assignments_for_desk(
            db,
            slot=slot,
            plan=plan,
            count=2,
            cp=object(),
            puller="gpu-01",
        )
        assert prompts == []
    finally:
        db.close()


def test_ai_gap_empty_standing_with_target(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.assignment_desk import ai_gap_for_desk

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        standing = upsert_standing_order(
            db,
            desk_path=slot.desk_path,
            shift_key=plan.shift_key,
            topics=[],
            target_count=5,
        )
        assert ai_gap_for_desk(db, slot=slot, standing=standing) == 5
    finally:
        db.close()


def test_update_roster_assignments(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.assignment_desk import update_roster_assignments

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        row = ShiftAssignment(
            shift_desk_slot_id=slot.id,
            prompt="AI topic",
            source="ai_suggested",
            status="pending",
        )
        db.add(row)
        db.commit()

        update_roster_assignments(
            db,
            plan_id=plan.id,
            updates=[
                {
                    "id": row.id,
                    "prompt": "Edited topic",
                    "locked": True,
                    "promote_to_manual": True,
                }
            ],
        )
        db.commit()
        db.refresh(row)
        assert row.prompt == "Edited topic"
        assert row.locked is True
        assert row.source == "manual"
    finally:
        db.close()


def test_approve_roster_without_generation_raises(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        plan.roster_review_status = "none"
        db.commit()
        with pytest.raises(ValueError, match="No generated roster"):
            approve_roster(db, plan_id=plan.id)
    finally:
        db.close()


def test_load_desk_brief(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.assignment_desk import load_desk_brief
    from article_factory.services.flow_storage import create_flow, read_flow, write_flow

    db = SessionLocal()
    try:
        missing = load_desk_brief(db, desk_path="missing/flow.json", flow_version_id=None)
        assert missing == ""

        rel_path, _ = create_flow(folder="brief", slug="brief-desk", display_name="Brief Desk", step_count=1)
        flow = read_flow(rel_path)
        flow.beat_brief = "Cover local sports beats."
        write_flow(rel_path, flow)
        loaded = load_desk_brief(db, desk_path=rel_path, flow_version_id=None)
        assert loaded == "Cover local sports beats."
    finally:
        db.close()


@pytest.mark.asyncio
async def test_process_t15_scheduler(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings
    from article_factory.services.shift_t15_scheduler import process_t15_due_plans

    db = SessionLocal()
    try:
        assert await process_t15_due_plans(db) == 0

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        _draft_plan(db, starts_in_minutes=10)
        db.commit()

        async def fake_t15(db, *, plan_id, cp, puller):
            return {"plan_id": plan_id}

        monkeypatch.setattr(
            "article_factory.services.shift_t15_scheduler.run_t15_for_plan",
            fake_t15,
        )
        pullers = [
            {
                "puller_name": "gpu-01",
                "supported_models": ["test-model"],
                "status": "idle",
                "is_active": True,
            }
        ]
        processed = await process_t15_due_plans(db, pullers=pullers)
        assert processed == 1
    finally:
        db.close()


def test_patch_roster_api(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = _draft_plan(db)
        slot = db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).one()
        row = ShiftAssignment(
            shift_desk_slot_id=slot.id,
            prompt="Original",
            source="manual",
            status="pending",
        )
        db.add(row)
        db.commit()
        plan_id = plan.id
        assignment_id = row.id
    finally:
        db.close()

    patched = client.patch(
        f"/api/shifts/plans/{plan_id}/roster",
        headers=api_headers,
        json={"assignments": [{"id": assignment_id, "prompt": "Updated", "locked": True}]},
    )
    assert patched.status_code == 200
    assignments = patched.json()["plan"]["desks"][0]["assignments"]
    assert assignments[0]["prompt"] == "Updated"
    assert assignments[0]["locked"] is True
