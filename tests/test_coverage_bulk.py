from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import article_factory.db as db_module
from article_factory.models import FactoryRun, StepExecution, TopicQueueItem
from article_factory.orchestrator.flow_runner import execute_flow_pipeline
from article_factory.orchestrator.runner import FactoryLoop, _execute_pipeline
from article_factory.services.flow_defaults import build_single_writer_flow, build_writer_review_flow
from article_factory.services.flow_schema import FlowStepCompletion, FlowStepLoop, new_flow_step
from article_factory.services.flow_storage import write_flow
from article_factory.services.queue_retry import is_queue_item_rerunnable
from article_factory.services.run_control import RunCancelledError, clear_run_cancel
from article_factory.services.step_trace import enrich_steps_with_responses
from article_factory.services.token_usage import enrich_step_record
from article_factory.services.control_plane_heartbeat import (
    ControlPlaneHeartbeatLoop,
    send_control_plane_heartbeats,
    _agent_display_name,
)
from article_factory.services.queue_presets import list_queue_presets


def _step_record(step_key: str, content: str) -> dict:
    return {
        "step_key": step_key,
        "step_name": step_key,
        "content": content,
        "duration_ms": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture(autouse=True)
async def _clear_cancel_flags():
    yield
    for run_id in ("run-flow-fail", "run-cancel-exec", "run-stop", "run-stop-api"):
        await clear_run_cancel(run_id)


def test_agent_display_name_with_label() -> None:
    run = FactoryRun(run_id="r", topic_slug="sports", status="running", flow_path="test/x.flow.json")
    name = _agent_display_name(run, "custom_key", "Custom Label")
    assert name == "Article Factory — Custom Label"


def test_is_queue_item_rerunnable_branches() -> None:
    item = TopicQueueItem(topic_slug="sports", prompt="p", status="queued")
    assert is_queue_item_rerunnable(item, None) is False

    running_item = TopicQueueItem(topic_slug="sports", prompt="p", status="running")
    running_run = FactoryRun(run_id="r", topic_slug="sports", status="running")
    assert is_queue_item_rerunnable(running_item, running_run) is False

    failed_run = FactoryRun(run_id="r", topic_slug="sports", status="failed")
    assert is_queue_item_rerunnable(running_item, failed_run) is True


def test_list_queue_presets(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        presets = list_queue_presets(db)
        assert isinstance(presets, list)
    finally:
        db.close()


def test_enrich_steps_no_run_returns_unchanged(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        steps = [{"step_key": "writer", "status": "completed"}]
        result = enrich_steps_with_responses(db, "no-such-run", steps)
        assert result == steps
    finally:
        db.close()


def test_enrich_step_record_tool_fallback() -> None:
    step = enrich_step_record(
        {
            "step_key": "writer",
            "prompt": "write",
            "tools_used": [{"tool": "web_fetch", "detail": "https://example.com"}],
        }
    )
    assert step["usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_send_control_plane_heartbeats_with_step(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-cp-step",
            topic_slug="sports",
            status="running",
            current_step="writer",
            selected_puller="p1",
            selected_model="m1",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="run-cp-step",
                step_key="writer",
                status="pulled",
                puller="p1",
                model="m1",
            )
        )
        db.commit()
        db.refresh(run)

        cp = AsyncMock()
        cp.post_node_heartbeat = AsyncMock()
        cp.post_agent_heartbeat = AsyncMock()
        await send_control_plane_heartbeats(
            cp,
            db=db,
            active_run=run,
            gateway_id="gw",
            gateway_display_name="Factory",
            extra_node_info={"extra": True},
        )
        node = cp.post_node_heartbeat.await_args.args[0]
        assert node["descriptive_info"]["extra"] is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_control_plane_heartbeat_loop_start_stop(monkeypatch) -> None:
    loop = ControlPlaneHeartbeatLoop()
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_tick",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.settings.heartbeat_interval_seconds",
        0.01,
    )
    await loop.start()
    await loop.stop()
    assert loop._task is None


@pytest.mark.asyncio
async def test_execute_flow_linear_single_writer(configured_db, monkeypatch) -> None:
    rel_path = "test/linear-single.flow.json"
    write_flow(rel_path, build_single_writer_flow())

    completed: dict = {}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return _step_record("writer", "# Title\n\nArticle body.")

    async def fake_complete(draft, records):
        completed["draft"] = draft

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
        run = FactoryRun(run_id="run-linear", topic_slug="sports", flow_path=rel_path, status="running")
        db.add(run)
        db.commit()
        from article_factory.services.runtime_settings import load_runtime_settings

        runtime = load_runtime_settings(db)
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path=rel_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=fake_complete,
        )
        assert completed["draft"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_missing_verdict_fails(configured_db, monkeypatch) -> None:
    rel_path = "test/flow-missing-verdict.flow.json"
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
        from article_factory.services.runtime_settings import update_factory_settings, load_runtime_settings

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


@pytest.mark.asyncio
async def test_execute_pipeline_run_cancelled_requeue(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="running", flow_path="test/x.flow.json")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-requeue",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
            flow_path="sports/standard-4-step.flow.json",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()

        async def boom(*args, **kwargs):
            raise RunCancelledError("stopped")

        monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
        monkeypatch.setattr(
            "article_factory.orchestrator.runner.take_requeue_flow_path",
            AsyncMock(return_value="test/requeue.flow.json"),
        )

        result = await _execute_pipeline(db, run=run, topic_prompt="Topic")
        db.refresh(item)
        assert item.status == "queued"
        assert item.flow_path == "test/requeue.flow.json"
        assert result.status == "cancelled"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_dispatch_no_pullers(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(return_value=[])
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()


@pytest.mark.asyncio
async def test_factory_loop_dispatch_stale_reservations(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("stale-puller")

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "puller-1",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            }
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.run_pipeline_for_topic",
        AsyncMock(),
    )

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    assert "stale-puller" not in loop._reserved_pullers or loop._reserved_pullers == set()


def test_personas_api(client, api_headers, configured_db) -> None:
    listing = client.get("/api/personas", headers=api_headers)
    assert listing.status_code == 200

    created = client.post(
        "/api/personas",
        headers=api_headers,
        json={"slug": "test-persona", "display_name": "Test", "system_prompt": "You are test."},
    )
    if created.status_code == 200:
        detail = client.get("/api/personas/test-persona", headers=api_headers)
        assert detail.status_code == 200
        deleted = client.delete("/api/personas/test-persona", headers=api_headers)
        assert deleted.status_code == 200

    missing = client.get("/api/personas/missing-persona", headers=api_headers)
    assert missing.status_code == 404


def test_flow_queues_routes(client, api_headers) -> None:
    listing = client.get("/api/flow-queues", headers=api_headers)
    assert listing.status_code == 200

    presets = client.get("/api/flow-queues/presets", headers=api_headers)
    assert presets.status_code == 200
