from datetime import datetime, timezone

from article_factory.models import Persona, ShiftAssignment, ShiftDeskSlot, ShiftPlan
from article_factory.services.persona_selection import (
    merge_persona_style_prompt,
    select_reporter_persona_slug,
)


def test_merge_persona_style_prompt_appends() -> None:
    merged = merge_persona_style_prompt("Base prompt.", "Write in a crisp newsroom voice.")
    assert merged.startswith("Base prompt.")
    assert "crisp newsroom voice" in merged


def test_round_robin_selection(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        plan = ShiftPlan(
            shift_key="morning",
            window_starts_at=datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc),
            window_ends_at=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
            status="active",
            default_model="test-model",
        )
        db.add(plan)
        db.flush()
        slot = ShiftDeskSlot(
            shift_plan_id=plan.id,
            name="Sports",
            desk_path="sports/standard-4-step.flow.json",
            reporter_selection_mode="round_robin",
        )
        db.add(slot)
        db.flush()

        for index, slug in enumerate(("reporter-a", "reporter-b")):
            db.add(
                ShiftAssignment(
                    shift_desk_slot_id=slot.id,
                    prompt=f"Topic {index}",
                    status="completed",
                    reporter_persona_slug=slug,
                    dispatched_at=datetime(2026, 7, 17, 7, index, tzinfo=timezone.utc),
                )
            )
        db.commit()

        next_slug = select_reporter_persona_slug(
            db,
            pool=["reporter-a", "reporter-b"],
            mode="round_robin",
            shift_plan_id=plan.id,
            desk_slot_id=slot.id,
        )
        assert next_slug == "reporter-a"
    finally:
        db.close()
