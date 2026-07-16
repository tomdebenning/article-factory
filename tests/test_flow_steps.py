from __future__ import annotations

from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_steps import flow_steps_payload, heartbeat_agents, step_display_name
from article_factory.services.flow_storage import write_flow


def _write_simple_test_flow() -> None:
    flow = new_flow_definition(slug="SimpleTest", display_name="SimpleTest", step_count=1)
    write_flow("test/SimpleTest.flow.json", flow)


def test_flow_steps_payload_simple_test(configured_db) -> None:
    _write_simple_test_flow()
    steps = flow_steps_payload("test/SimpleTest.flow.json")
    assert len(steps) == 1
    assert steps[0]["step_key"] == "step_1"
    assert steps[0]["label"] == "Step 1"


def test_flow_steps_payload_standard_four_step(configured_db) -> None:
    steps = flow_steps_payload("sports/standard-4-step.flow.json")
    assert len(steps) == 4
    assert [step["step_key"] for step in steps] == [
        "writer",
        "fact_asserter",
        "source_finder",
        "review",
    ]


def test_flow_steps_payload_empty_and_missing_flow() -> None:
    assert flow_steps_payload("") == []
    assert flow_steps_payload("does/not/exist.flow.json") == []


def test_heartbeat_agents_legacy_fallback(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        agents = heartbeat_agents(db, None)
        assert agents[0]["step_key"] == "writer"
        assert agents[0]["display_name"] == "Writer"
    finally:
        db.close()


def test_step_display_name_paths(configured_db) -> None:
    assert step_display_name("sports/standard-4-step.flow.json", "writer") == "Writer"
    assert step_display_name(None, "writer") == "Writer"
    assert step_display_name(None, "custom_step") == "custom step"


def test_heartbeat_agents_use_active_run_flow(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun

    _write_simple_test_flow()
    db = SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-simple",
            topic_slug="general",
            flow_path="test/SimpleTest.flow.json",
            status="running",
            current_step="step_1",
        )
        agents = heartbeat_agents(db, run)
        assert len(agents) == 1
        assert agents[0]["step_key"] == "step_1"
    finally:
        db.close()
