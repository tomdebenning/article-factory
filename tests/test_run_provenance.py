from datetime import datetime, timezone

from article_factory.models import FactoryRun, ShiftAssignment, ShiftDeskSlot, ShiftPlan
from article_factory.services.run_provenance import enrich_manifest_with_run_context


def test_enrich_manifest_with_shift_desk_and_reporter(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        starts = datetime(2026, 7, 18, 6, 0, tzinfo=timezone.utc)
        plan = ShiftPlan(
            shift_key="morning",
            window_starts_at=starts,
            window_ends_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            status="active",
            default_model="test-model",
        )
        db.add(plan)
        db.flush()
        slot = ShiftDeskSlot(
            shift_plan_id=plan.id,
            name="Sports Desk",
            desk_path="test/debug-verdict.flow.json",
            topic_slug="sports",
        )
        db.add(slot)
        db.flush()
        assignment = ShiftAssignment(
            shift_desk_slot_id=slot.id,
            prompt="Topic",
            reporter_persona_slug="reporter-a",
            status="running",
        )
        db.add(assignment)
        db.flush()
        run = FactoryRun(
            run_id="prov-run",
            topic_slug="sports",
            status="running",
            shift_plan_id=plan.id,
            shift_assignment_id=assignment.id,
            reporter_persona_slug="reporter-a",
            reporter_persona_name="Alex",
        )
        db.add(run)
        db.commit()

        manifest = {"steps": [{"step_key": "writer", "label": "Reporter"}]}
        enriched = enrich_manifest_with_run_context(db, run, manifest)

        assert enriched["reported_by"] == "Alex"
        assert enriched["shift_key"] == "morning"
        assert "Morning Shift" in enriched["shift_label"]
        assert enriched["desk_name"] == "Sports Desk"
        assert enriched["steps"][0]["persona_name"] == "Alex"
    finally:
        db.close()


def test_enrich_manifest_minimal_run(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        run = FactoryRun(run_id="bare-run", topic_slug="general", status="running")
        db.add(run)
        db.commit()
        enriched = enrich_manifest_with_run_context(db, run, {"step_stats": []})
        assert "shift_key" not in enriched
        assert "reported_by" not in enriched
    finally:
        db.close()
