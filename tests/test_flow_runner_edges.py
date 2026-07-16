from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import article_factory.db as db_module
from article_factory.models import FactoryRun
from article_factory.orchestrator.flow_runner import (
    default_flow_path_for_topic,
    execute_flow_pipeline,
    restore_flow_state,
    step_index_by_id,
)
from article_factory.services.flow_defaults import build_writer_review_flow
from article_factory.services.flow_schema import FlowStepCompletion, new_flow_step
from article_factory.services.flow_storage import write_flow
from article_factory.services.runtime_settings import load_runtime_settings


def _step_record(step_key: str, content: str) -> dict:
    return {
        "step_key": step_key,
        "step_name": step_key,
        "content": content,
        "duration_ms": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "completed_at": "2026-01-01T00:00:00Z",
    }


def test_restore_flow_state_legacy_fields() -> None:
    run = FactoryRun(
        run_id="run-legacy",
        topic_slug="sports",
        status="running",
        pipeline_state={
            "draft": "Draft body",
            "sources": "Sources",
            "fact_check": "Facts",
            "step_records": [],
            "iteration": 2,
        },
        review_round=2,
    )
    outputs, feedback, records, iteration, step_id = restore_flow_state(run)
    assert outputs["writer"] == "Draft body"
    assert outputs["source_finder"] == "Sources"
    assert outputs["fact_asserter"] == "Facts"
    assert iteration == 2


def test_step_index_by_id_unknown() -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    with pytest.raises(ValueError, match="Unknown step_id"):
        step_index_by_id([writer], "missing-id")


def test_default_flow_path_for_topic(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        path = default_flow_path_for_topic("sports", db)
        assert path.endswith(".flow.json")
    finally:
        db.close()


def test_default_flow_path_for_topic_opens_session(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"default_flow_path": "sports/standard-4-step.flow.json"})
    finally:
        db.close()
    path = default_flow_path_for_topic("sports", None)
    assert "standard-4-step" in path


@pytest.mark.asyncio
async def test_execute_flow_pipeline_reject_then_accept(configured_db, monkeypatch) -> None:
    rel_path = "test/flow-runner-review.flow.json"
    write_flow(rel_path, build_writer_review_flow())
    review_calls = {"n": 0}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody")
        review_calls["n"] += 1
        if review_calls["n"] == 1:
            return _step_record("review", "Fix it.\n\nVERDICT: REJECT")
        return _step_record("review", "Good.\n\nVERDICT: ACCEPT")

    async def fake_select(_cp, model: str) -> str:
        return "puller-1"

    completed: dict[str, str] = {}

    async def complete_run(draft: str, step_records: list) -> None:
        completed["draft"] = draft

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(
            run_id="run-flow-runner",
            topic_slug="sports",
            flow_path=rel_path,
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path=rel_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=complete_run,
        )
        assert result.review_round >= 1
        assert completed["draft"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_pipeline_missing_verdict_fails(configured_db, monkeypatch) -> None:
    rel_path = "test/flow-runner-fail.flow.json"
    write_flow(rel_path, build_writer_review_flow())

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody")
        return _step_record("review", "No verdict here")

    async def fake_select(_cp, model: str) -> str:
        return "puller-1"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(
            run_id="run-flow-fail",
            topic_slug="sports",
            flow_path=rel_path,
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path=rel_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert result.status == "failed"
        assert "VERDICT" in (result.error or "")
    finally:
        db.close()
