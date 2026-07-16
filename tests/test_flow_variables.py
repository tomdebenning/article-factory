from __future__ import annotations

from article_factory.services.flow_schema import FlowStep
from article_factory.services.flow_variables import build_step_variables


def test_build_step_variables_sets_draft_from_article_step_id() -> None:
    essayist = FlowStep(
        step_id="essay-id",
        order=1,
        step_key="step_1",
        label="Essayist",
    )
    editor = FlowStep(
        step_id="editor-id",
        order=2,
        step_key="step_2",
        label="Editor",
    )
    variables = build_step_variables(
        topic="Big Ten football",
        feedback="",
        step_outputs={"essay-id": "Article body here.", "step_1": "Article body here."},
        steps=[essayist],
        article_step_id="essay-id",
    )
    assert variables["draft"] == "Article body here."
    assert variables["step_1"] == "Article body here."


def test_build_step_variables_falls_back_to_writer_key() -> None:
    writer = FlowStep(
        step_id="writer-id",
        order=1,
        step_key="writer",
        label="Writer",
    )
    variables = build_step_variables(
        topic="Topic",
        feedback="",
        step_outputs={"writer-id": "Legacy draft.", "writer": "Legacy draft."},
        steps=[writer],
        article_step_id=None,
    )
    assert variables["draft"] == "Legacy draft."
