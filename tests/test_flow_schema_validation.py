from __future__ import annotations

import pytest

from article_factory.services.flow_schema import (
    FlowDefinition,
    FlowStep,
    FlowStepCompletion,
    FlowStepLoop,
    flow_from_dict,
    flow_to_dict,
    new_flow_definition,
    new_flow_step,
    slugify_step_key,
    strip_runtime_overrides,
)


def _base_steps() -> list[FlowStep]:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    return [writer, review]


def test_slugify_step_key_empty_label() -> None:
    assert slugify_step_key("!!!", 3) == "step_3"


def test_flow_validation_duplicate_order() -> None:
    steps = _base_steps()
    steps[1].order = 1
    with pytest.raises(ValueError, match="contiguous"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_duplicate_step_id() -> None:
    steps = _base_steps()
    steps[1].step_id = steps[0].step_id
    with pytest.raises(ValueError, match="Duplicate step_id"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_duplicate_step_key() -> None:
    steps = _base_steps()
    steps[1].step_key = steps[0].step_key
    with pytest.raises(ValueError, match="Duplicate step_key"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_non_contiguous_order() -> None:
    steps = _base_steps()
    steps[1].order = 3
    with pytest.raises(ValueError, match="contiguous"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_last_step_must_complete_or_loop() -> None:
    steps = _base_steps()
    steps[-1].completion = FlowStepCompletion(can_complete=False, can_loop=False)
    with pytest.raises(ValueError, match="Last step must allow"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_loop_requires_goto() -> None:
    steps = _base_steps()
    steps[-1].completion = FlowStepCompletion(can_complete=True, can_loop=True)
    with pytest.raises(ValueError, match="loop_goto_step_id"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_loop_missing_target() -> None:
    steps = _base_steps()
    steps[-1].completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id="missing-id",
    )
    with pytest.raises(ValueError, match="references a missing step"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_loop_to_self() -> None:
    steps = _base_steps()
    steps[-1].completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=steps[-1].step_id,
    )
    with pytest.raises(ValueError, match="cannot loop to itself"):
        FlowDefinition(slug="t", display_name="T", steps=steps)


def test_flow_validation_mid_step_loop_errors() -> None:
    steps = _base_steps()

    steps[1].loop = FlowStepLoop(enabled=True)
    with pytest.raises(ValueError, match="loop requires goto_step_id"):
        FlowDefinition(slug="t", display_name="T", steps=steps)

    steps[1].loop = FlowStepLoop(enabled=True, goto_step_id="missing")
    with pytest.raises(ValueError, match="references a missing step"):
        FlowDefinition(slug="t", display_name="T", steps=steps)

    steps[1].loop = FlowStepLoop(enabled=True, goto_step_id=steps[1].step_id)
    with pytest.raises(ValueError, match="cannot loop to itself"):
        FlowDefinition(slug="t", display_name="T", steps=steps)

    extra = new_flow_step(order=3, label="Extra", step_key="extra")
    steps_with_extra = [steps[0], steps[1], extra]
    steps_with_extra[1].loop = FlowStepLoop(enabled=True, goto_step_id=extra.step_id)
    with pytest.raises(ValueError, match="must loop back to an earlier step"):
        FlowDefinition(slug="t", display_name="T", steps=steps_with_extra)


def test_flow_validation_article_step_id() -> None:
    steps = _base_steps()
    with pytest.raises(ValueError, match="article_step_id references"):
        FlowDefinition(slug="t", display_name="T", steps=steps, article_step_id="missing")

    flow = FlowDefinition(slug="t", display_name="T", steps=steps)
    assert flow.article_step_id == steps[0].step_id


def test_new_flow_definition_single_step() -> None:
    flow = new_flow_definition(slug="solo", display_name="Solo", step_count=1)
    assert len(flow.steps) == 1
    assert flow.steps[0].completion is not None


def test_flow_round_trip_and_strip_overrides() -> None:
    flow = new_flow_definition(slug="rt", display_name="RT", step_count=2)
    flow.steps[0].model = "m1"
    flow.steps[0].puller = "p1"
    data = flow_to_dict(flow)
    restored = flow_from_dict(data)
    stripped = strip_runtime_overrides(restored)
    assert stripped.steps[0].model == ""
    assert stripped.steps[0].puller == ""
