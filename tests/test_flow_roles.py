from __future__ import annotations

from datetime import datetime, timezone

from article_factory.services.flow_roles import group_steps_into_iterations, resolve_flow_roles
from article_factory.services.flow_schema import FlowDefinition, FlowStepCompletion, new_flow_step


def _step_1_step_2_flow() -> FlowDefinition:
    writer = new_flow_step(order=1, label="BetterWriter", step_key="step_1")
    review = new_flow_step(order=2, label="Editor", step_key="step_2")
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    return FlowDefinition(slug="test", display_name="Test", steps=[writer, review])


def test_resolve_gate_step_for_step_2_flow() -> None:
    roles = resolve_flow_roles(_step_1_step_2_flow())
    assert roles.gate_step_key == "step_2"
    assert roles.producer_step_keys == ["step_1"]


def test_group_multi_pass_iterations() -> None:
    roles = resolve_flow_roles(_step_1_step_2_flow())
    records = [
        {"step_key": "step_1", "content": "draft 1"},
        {"step_key": "step_2", "content": "VERDICT: REJECTED"},
        {"step_key": "step_1", "content": "draft 2"},
        {"step_key": "step_2", "content": "VERDICT: ACCEPTED"},
    ]
    groups = group_steps_into_iterations(records, roles)
    assert len(groups) == 2
    assert groups[0].writer_records[0]["content"] == "draft 1"
    assert groups[1].reviewer_record is not None
