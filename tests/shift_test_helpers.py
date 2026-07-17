from __future__ import annotations

from datetime import datetime, timezone


def seed_active_shift_assignments(
    db,
    *,
    prompts: list[str] | None = None,
    model: str = "test-model",
    flow_path: str = "sports/standard-4-step.flow.json",
    flow_version_id: int | None = None,
):
    from article_factory.models import ShiftAssignment, ShiftDeskSlot, ShiftPlan
    from article_factory.services.shift_windows import today_and_tomorrow_shift_windows

    assignment_prompts = prompts or ["From queue"]
    window = today_and_tomorrow_shift_windows()[0]
    plan = ShiftPlan(
        shift_key=window.shift_key,
        window_starts_at=window.starts_at,
        window_ends_at=window.ends_at,
        status="active",
        default_model=model,
        activated_at=datetime.now(timezone.utc),
    )
    db.add(plan)
    db.flush()
    slot = ShiftDeskSlot(
        shift_plan_id=plan.id,
        name="Test desk",
        desk_path=flow_path,
        topic_slug="sports",
        flow_version_id=flow_version_id,
    )
    db.add(slot)
    db.flush()
    for prompt in assignment_prompts:
        db.add(
            ShiftAssignment(
                shift_desk_slot_id=slot.id,
                prompt=prompt,
                status="pending",
            )
        )
    db.commit()
    return plan, slot
