from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import article_factory.db as db_module
from article_factory.models import FactoryRun
from article_factory.orchestrator.flow_runner import (
    default_flow_path_for_topic,
    execute_flow_pipeline,
    restore_flow_state,
    sorted_steps,
    step_index_by_id,
)
from article_factory.services.flow_schema import FlowDefinition, FlowStepCompletion, FlowStepLoop, new_flow_step
from article_factory.services.flow_storage import write_flow
from article_factory.services.flow_versions import create_flow_version
from article_factory.services.runtime_settings import load_runtime_settings


def _step_record(step_key: str, content: str) -> dict:
    return {
        "step_key": step_key,
        "content": content,
        "duration_ms": 1,
        "usage": {"total_tokens": 2},
    }


@pytest.fixture
def flow_runner_env(configured_db, monkeypatch) -> None:
    async def fake_select(_cp, model: str) -> str:
        return "puller-1"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)


@pytest.mark.asyncio
async def test_execute_flow_missing_verdict_on_last_step(configured_db, flow_runner_env, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(can_complete=True, can_loop=True, loop_goto_step_id=writer.step_id)
    flow = FlowDefinition(slug="missing-verdict", display_name="MV", article_step_id=writer.step_id, steps=[writer, review])
    write_flow("missing-verdict.flow.json", flow)

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "No verdict here")
        return _step_record(ctx.step_key, "# Draft\n\nBody")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, "missing-verdict.flow.json", flow=flow, message="v1")
        run = FactoryRun(
            run_id="run-mv",
            topic_slug="general",
            flow_path="missing-verdict.flow.json",
            flow_version_id=version.id,
            status="running",
            selected_model="test-model",
        )
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)

        async def emit(_step):
            return None

        async def complete(_draft, _records):
            return None

        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="missing-verdict.flow.json",
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=emit,
            complete_run=complete,
        )
        assert result.status == "failed"
        assert "missing VERDICT" in (result.error or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_reject_without_loop_target(configured_db, flow_runner_env, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion.model_construct(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id="",
    )
    flow = FlowDefinition.model_construct(
        slug="no-loop",
        display_name="No Loop",
        article_step_id=writer.step_id,
        steps=[writer, review],
        max_iterations=3,
    )

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "Bad.\n\nVERDICT: REJECT")
        return _step_record(ctx.step_key, "# Draft")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.resolve_flow_for_run", lambda db, run: flow)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-no-loop",
            topic_slug="general",
            flow_path="no-loop.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="no-loop.flow.json",
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert result.status == "failed"
        assert "no loop target" in (result.error or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_mid_step_loop(configured_db, flow_runner_env, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.loop = FlowStepLoop(enabled=True, goto_step_id=writer.step_id)
    finalize = new_flow_step(order=3, label="Finalize", step_key="finalize")
    finalize.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    flow = FlowDefinition(
        slug="mid-loop",
        display_name="Mid Loop",
        article_step_id=writer.step_id,
        steps=[writer, review, finalize],
    )
    write_flow("mid-loop.flow.json", flow)
    review_calls = {"n": 0}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            review_calls["n"] += 1
            if review_calls["n"] == 1:
                return _step_record("review", "Fix it.\n\nVERDICT: REJECT")
            return _step_record("review", "Good.\n\nVERDICT: ACCEPT")
        if ctx.step_key == "writer":
            return _step_record("writer", "# Draft v2")
        return _step_record("finalize", "# Final")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-mid-loop",
            topic_slug="general",
            flow_path="mid-loop.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        completed = AsyncMock()
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="mid-loop.flow.json",
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=completed,
        )
        assert review_calls["n"] == 2
        assert completed.await_count == 1
    finally:
        db.close()


def test_restore_flow_state_legacy_fields() -> None:
    run = FactoryRun(
        run_id="legacy",
        topic_slug="general",
        status="running",
        current_step="writer",
        review_round=2,
        pipeline_state={
            "draft": "legacy draft",
            "sources": "legacy sources",
            "fact_check": "legacy facts",
            "step_records": [{"step_key": "writer", "content": "x"}],
            "current_step_key_map": {"writer": "step-id-1"},
        },
    )
    outputs, feedback, records, iteration, resume_id = restore_flow_state(run)
    assert outputs["writer"] == "legacy draft"
    assert outputs["source_finder"] == "legacy sources"
    assert outputs["fact_asserter"] == "legacy facts"
    assert iteration == 2
    assert resume_id == "step-id-1"
    assert len(records) == 1


def test_sorted_steps_and_step_index() -> None:
    first = new_flow_step(order=1, label="A", step_key="a")
    second = new_flow_step(order=2, label="B", step_key="b")
    flow = FlowDefinition(slug="s", display_name="S", steps=[second, first])
    ordered = sorted_steps(flow)
    assert ordered[0].step_key == "a"
    assert step_index_by_id(ordered, first.step_id) == 0
    with pytest.raises(ValueError):
        step_index_by_id(ordered, "missing")


def test_default_flow_path_for_topic(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        path = default_flow_path_for_topic("ignored", db=db)
        assert path.endswith(".flow.json")
    finally:
        db.close()
