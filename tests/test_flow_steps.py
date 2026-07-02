from __future__ import annotations

from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_steps import flow_steps_payload, heartbeat_agents
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
