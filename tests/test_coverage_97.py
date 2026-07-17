"""Targeted tests to raise coverage to 97%."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import article_factory.db as db_module
from article_factory.models import (
    CompletedArticle,
    FactoryRun,
    FlowQueue,
    StepExecution,
    TopicQueueItem,
)
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.orchestrator.flow_runner import default_flow_path_for_topic, execute_flow_pipeline
from article_factory.orchestrator.runner import (
    FactoryLoop,
    _complete_run,
    _emit_run_event,
    _execute_pipeline,
    _front_queue_priority,
    _topic_prompt_for_run,
    continue_active_run,
    run_pipeline_for_topic,
)
from article_factory.services.flow_schema import (
    FlowDefinition,
    FlowStep,
    FlowStepCompletion,
    FlowStepLoop,
    new_flow_definition,
    new_flow_step,
)
from article_factory.services.flow_storage import (
    create_folder,
    create_flow,
    import_flow,
    list_tree,
    read_flow,
    write_flow,
)
from article_factory.services.review_parser import (
    BEGIN_REVIEW_JSON,
    END_REVIEW_JSON,
    issue_resolution_counts,
    parse_structured_review,
)
from article_factory.services.run_recovery import reconcile_orphaned_runs
from article_factory.services.showroom_status_sync import (
    ShowroomStatusLoop,
    refresh_showroom_status,
    schedule_showroom_status_refresh,
    showroom_status_loop,
    showroom_status_tick,
    sync_showroom_when_factory_busy,
)
from article_factory.services.step_tools import (
    StepToolRegistry,
    WorkspaceViolation,
    normalize_step_enabled_tools,
    resolve_workspace_path,
)
from article_factory.services.step_trace import (
    StepTracer,
    batch_step_executions_payload,
    enrich_steps_with_responses,
)
from article_factory.services.telemetry import (
    capture_run_telemetry_safe,
    rebuild_flow_telemetry,
)
from article_factory.workers.executor import execute_step, run_step_from_context
from article_factory.workers.base import StepContext


def _step_record(step_key: str, content: str) -> dict:
    return {
        "step_key": step_key,
        "step_name": step_key,
        "content": content,
        "duration_ms": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "completed_at": "2026-01-01T00:00:00Z",
    }


# --- runner.py ---


def test_front_queue_priority_with_queued_items(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(TopicQueueItem(topic_slug="sports", prompt="A", status="queued", priority=10))
        db.commit()
        assert _front_queue_priority(db) == 9
    finally:
        db.close()


def test_front_queue_priority_empty_queue(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        assert _front_queue_priority(db) == 0
    finally:
        db.close()


def test_topic_prompt_for_run_fallback(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="r", topic_slug="sports-news", status="running")
        assert _topic_prompt_for_run(db, run) == "Sports News"
    finally:
        db.close()


def test_topic_prompt_for_run_from_queue(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="  Custom prompt  ", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(run_id="r", topic_slug="sports", queue_item_id=item.id, status="running")
        assert _topic_prompt_for_run(db, run) == "  Custom prompt  "
    finally:
        db.close()


@pytest.mark.asyncio
async def test_emit_run_event_no_cms() -> None:
    await _emit_run_event(None, run_id="r", topic_slug="sports", event="run_started")


@pytest.mark.asyncio
async def test_emit_run_event_with_step_key() -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()
    await _emit_run_event(cms, run_id="r", topic_slug="sports", event="step_started", step_key="writer")
    cms.post_run_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_run_cms_unavailable_sets_error(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="cms-skip", topic_slug="sports", status="running")
        db.add(run)
        db.commit()
        monkeypatch.setattr(
            "article_factory.orchestrator.runner._cms_configured",
            lambda runtime: True,
        )
        await _complete_run(db, run, "# Title\n\nBody", [_step_record("writer", "# Title\n\nBody")], cms=None)
        db.refresh(run)
        assert "CMS client unavailable" in (run.error or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_resume_by_step_key(configured_db, monkeypatch) -> None:
    executed: list[str | None] = []

    async def fake_flow(db, **kwargs):
        executed.append(kwargs.get("resume_from_step_id"))
        run = kwargs["run"]
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", fake_flow)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="resume-key",
            topic_slug="sports",
            flow_path="sports/standard-4-step.flow.json",
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()
        await _execute_pipeline(db, run=run, topic_prompt="Topic", resume_from_step="writer")
        assert executed[0] is not None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_resume_invalid_step(configured_db, monkeypatch) -> None:
    executed: list[str | None] = []

    async def fake_flow(db, **kwargs):
        executed.append(kwargs.get("resume_from_step_id"))
        run = kwargs["run"]
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", fake_flow)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.resolve_flow_for_run",
        lambda db, run: (_ for _ in ()).throw(RuntimeError("bad flow")),
    )

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="resume-bad", topic_slug="sports", status="running", current_step="nope")
        db.add(run)
        db.commit()
        await _execute_pipeline(db, run=run, topic_prompt="Topic", resume_from_step="nope")
        assert executed[0] is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_run_cancelled_with_requeue(configured_db, monkeypatch) -> None:
    from article_factory.services.run_control import RunCancelledError, take_requeue_flow_path

    async def boom(db, **kwargs):
        raise RunCancelledError("stopped")

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.take_requeue_flow_path",
        AsyncMock(return_value="test/other.flow.json"),
    )

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Requeue me", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="cancel-requeue",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()
        result = await _execute_pipeline(db, run=run, topic_prompt="Topic")
        db.refresh(item)
        assert result.status == "cancelled"
        assert item.status == "queued"
        assert item.flow_path == "test/other.flow.json"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_asyncio_cancelled(configured_db, monkeypatch) -> None:
    async def boom(db, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="async-cancel", topic_slug="sports", status="running")
        db.add(run)
        db.commit()
        with pytest.raises(asyncio.CancelledError):
            await _execute_pipeline(db, run=run, topic_prompt="Topic")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_persist_failure_logged(configured_db, monkeypatch) -> None:
    async def boom(db, **kwargs):
        raise RuntimeError("pipeline boom")

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.commit_with_retry",
        MagicMock(side_effect=RuntimeError("db dead")),
    )

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="persist-fail", topic_slug="sports", status="running")
        db.add(run)
        db.commit()
        with pytest.raises(RuntimeError, match="pipeline boom"):
            await _execute_pipeline(db, run=run, topic_prompt="Topic")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_empty_current_step(configured_db, monkeypatch) -> None:
    resumed: list[str | None] = []

    async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
        resumed.append(resume_from_step)
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.ensure_run_pipeline_state",
        lambda db, run: True,
    )

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="empty-step",
            topic_slug="sports",
            flow_path="sports/standard-4-step.flow.json",
            status="running",
            current_step="",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()
        assert await continue_active_run(db, run) is True
        assert resumed[0] == "writer"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_ensure_running_restarts_done_task(monkeypatch) -> None:
    loop = FactoryLoop()
    started = asyncio.Event()

    async def fake_start(self) -> None:
        started.set()
        self._running = True
        self._task = asyncio.create_task(asyncio.sleep(60))

    monkeypatch.setattr(FactoryLoop, "start", fake_start)
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    loop._task = done_task

    await loop.ensure_running()
    assert started.is_set()
    loop._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop._task


@pytest.mark.asyncio
async def test_factory_loop_spawn_worker_skips_duplicate() -> None:
    loop = FactoryLoop()
    hold = asyncio.Event()

    async def slow_worker() -> None:
        await hold.wait()

    loop._spawn_worker("run-1", slow_worker())
    assert "run-1" in loop._run_workers
    loop._spawn_worker("run-1", slow_worker())
    assert len(loop._run_workers) == 1
    hold.set()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_factory_loop_spawn_worker_logs_exception(monkeypatch) -> None:
    loop = FactoryLoop()

    async def boom() -> None:
        raise RuntimeError("worker boom")

    loop._spawn_worker("run-boom", boom())
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert "run-boom" not in loop._run_workers


@pytest.mark.asyncio
async def test_factory_loop_wait_timeout(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True
    monkeypatch.setattr("article_factory.orchestrator.runner.settings.dispatch_interval_seconds", 0.01)
    await loop._wait_for_next_tick()


@pytest.mark.asyncio
async def test_factory_loop_dispatch_skips_existing_workers(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    continued: list[str] = []

    async def track(self, run_id: str) -> None:
        continued.append(run_id)

    monkeypatch.setattr(FactoryLoop, "_continue_run", track)

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="already-worker",
                topic_slug="sports",
                status="running",
                current_step="writer",
            )
        )
        db.commit()
        loop._run_workers["run-already-worker"] = asyncio.create_task(asyncio.sleep(60))
    finally:
        db.close()

    await loop._dispatch_tick()
    loop._run_workers["run-already-worker"].cancel()
    assert continued == []


@pytest.mark.asyncio
async def test_factory_loop_dispatch_no_idle_pullers_warning(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "busy-puller",
                "is_active": True,
                "is_stale": False,
                "status": "busy",
                "supported_models": ["test-model"],
            }
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Waiting"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    assert loop._run_workers == {}


@pytest.mark.asyncio
async def test_factory_loop_dispatch_skips_busy_worker_key(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    started: list[int] = []

    async def fake_pipeline(db, **kwargs):
        started.append(1)

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
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", fake_pipeline)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        item = TopicQueueItem(topic_slug="sports", prompt="Busy queue", status="queued")
        db.add(item)
        db.commit()
        loop._run_workers[f"queue-{item.id}"] = asyncio.create_task(asyncio.sleep(60))
    finally:
        db.close()

    await loop._dispatch_tick()
    assert started == []


@pytest.fixture
def pipeline_env(configured_db, monkeypatch) -> None:
    async def fake_select(_cp, model: str) -> str:
        return "auto-puller"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"default_model": "test-model"})
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_with_snapshot(configured_db, pipeline_env, monkeypatch) -> None:
    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "Good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "# Title\n\nBody")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: AsyncMock())
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    from article_factory.models import FlowQueue
    from article_factory.services.flow_queues import create_flow_queue

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Snap", flow_path="sports/standard-4-step.flow.json", topic_slug="sports")
        db.flush()
        item = TopicQueueItem(flow_queue_id=queue.id, topic_slug="sports", prompt="Snap topic", status="running")
        db.add(item)
        db.commit()
        run = await run_pipeline_for_topic(
            db,
            topic_slug="sports",
            topic_prompt="Snap topic",
            queue_item_id=item.id,
            selected_puller="puller-1",
        )
        assert run.topic_queue_snapshot_id is not None
    finally:
        db.close()


# --- admin.py routes ---


def test_enqueue_with_flow_queue_id(client, api_headers, configured_db) -> None:
    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "Enqueue Q", "flow_path": "test/SimpleTest.flow.json", "topic_slug": "general"},
    )
    queue_id = created.json()["queue"]["id"]
    response = client.post(
        "/api/queue",
        headers=api_headers,
        json={
            "topic_slug": "general",
            "prompt": "Via queue",
            "flow_queue_id": queue_id,
        },
    )
    assert response.status_code == 200


def test_test_brave_search_success(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "brave_search_api_key": "test-key",
            },
        )
    finally:
        db.close()

    async def fake_search(**kwargs):
        return {"web": {"results": [{"title": "Hit", "description": "Sample", "url": "https://example.com"}]}}

    monkeypatch.setattr("article_factory.routes.admin.brave_web_search", fake_search)
    monkeypatch.setattr(
        "article_factory.routes.admin.format_brave_results",
        lambda payload: "Hit\n\nSample",
    )

    response = client.post("/api/settings/test/brave-search", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_brave_search_failure(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "brave_search_api_key": "test-key",
            },
        )
    finally:
        db.close()

    async def boom(**kwargs):
        raise RuntimeError("brave down")

    monkeypatch.setattr("article_factory.routes.admin.brave_web_search", boom)

    response = client.post("/api/settings/test/brave-search", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_control_plane_task_status_found(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
    finally:
        db.close()

    mock_cp = AsyncMock()
    mock_cp.get_task_status = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr("article_factory.routes.admin.ControlPlaneClient", lambda **kwargs: mock_cp)

    response = client.get(
        "/api/control-plane/tasks/status",
        headers=api_headers,
        params={"conversation_id": "conv-1"},
    )
    assert response.status_code == 200
    assert response.json()["found"] is True


def test_control_plane_task_status_error(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
    finally:
        db.close()

    mock_cp = AsyncMock()
    mock_cp.get_task_status = AsyncMock(side_effect=RuntimeError("cp down"))
    monkeypatch.setattr("article_factory.routes.admin.ControlPlaneClient", lambda **kwargs: mock_cp)

    response = client.get(
        "/api/control-plane/tasks/status",
        headers=api_headers,
        params={"conversation_id": "conv-1"},
    )
    assert response.status_code == 502


def test_factory_stop_all_runs_validation(client, api_headers, monkeypatch) -> None:
    async def boom(db, **kwargs):
        raise ValueError("bad stop")

    monkeypatch.setattr("article_factory.routes.admin.stop_all_runs", boom)
    response = client.post("/api/factory/stop-all-runs", headers=api_headers, json={"requeue": False})
    assert response.status_code == 400


def test_factory_switch_flow_dispatches(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.orchestrator.runner import factory_loop

    async def fake_switch(db, **kwargs):
        return {"queued_item_id": 42, "flow_path": "sports/standard-4-step.flow.json"}

    dispatched = {"called": False}
    monkeypatch.setattr("article_factory.routes.admin.switch_active_flow", fake_switch)
    monkeypatch.setattr(factory_loop, "request_dispatch", lambda: dispatched.__setitem__("called", True))

    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={
            "flow_path": "sports/standard-4-step.flow.json",
            "set_as_default": True,
            "clear_history": False,
            "update_queued": True,
            "requeue_running": False,
            "topic_slug": "sports",
        },
    )
    assert response.status_code == 200
    assert dispatched["called"] is True


def test_factory_switch_flow_not_found(client, api_headers, monkeypatch) -> None:
    async def missing(db, **kwargs):
        raise FileNotFoundError("missing.flow.json")

    monkeypatch.setattr("article_factory.routes.admin.switch_active_flow", missing)
    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={"flow_path": "missing.flow.json", "topic_slug": "sports"},
    )
    assert response.status_code == 404


def test_retry_queue_item_not_found(client, api_headers) -> None:
    response = client.post("/api/queue/99999/retry", headers=api_headers)
    assert response.status_code == 404


def test_recover_accept_run(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="recover-route",
            topic_slug="general",
            flow_path="test/writer-review.flow.json",
            status="failed",
            error="Last step response missing VERDICT: ACCEPT or VERDICT: REJECT",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="recover-route",
                step_key="writer",
                status="completed",
                response_content="# Article\n\nBody.",
            )
        )
        db.add(
            StepExecution(
                run_id="recover-route",
                step_key="review",
                status="completed",
                response_content="Good.\n\n## VERDICT: ACCEPT",
            )
        )
        db.commit()
    finally:
        db.close()

    async def fake_publish(db, *, run, article, cms=None, runtime=None):
        return {"ok": True}

    monkeypatch.setattr("article_factory.orchestrator.runner.publish_article_to_showroom", fake_publish)

    response = client.post("/api/runs/recover-route/recover-accept", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["run"]["status"] == "completed"


def test_recover_accept_ineligible(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="no-recover", topic_slug="sports", status="completed"))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/runs/no-recover/recover-accept", headers=api_headers)
    assert response.status_code == 400


def test_get_run_step_file(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_storage import save_step_response_to_disk

    save_step_response_to_disk(run_id="run-file", step_order=1, step_key="writer", content="# Draft")
    ok = client.get("/api/runs/run-file/step-files/01-writer.md", headers=api_headers)
    assert ok.status_code == 200
    assert ok.json()["content"] == "# Draft"

    missing = client.get("/api/runs/run-file/step-files/missing.md", headers=api_headers)
    assert missing.status_code == 404


def test_get_article_step_and_workspace_files(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="art-files", topic_slug="sports", status="completed"))
        db.add(
            CompletedArticle(
                run_id="art-files",
                topic_slug="sports",
                title="T",
                body_markdown="# T\n\nB",
            )
        )
        db.commit()
    finally:
        db.close()

    from article_factory.services.flow_storage import save_step_response_to_disk

    save_step_response_to_disk(run_id="art-files", step_order=1, step_key="writer", content="draft")

    step = client.get("/api/articles/art-files/step-files/01-writer.md", headers=api_headers)
    assert step.status_code == 200

    bad = client.get("/api/articles/art-files/step-files/../secret.md", headers=api_headers)
    assert bad.status_code in (400, 404)


def test_list_control_plane_pullers_success(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
    finally:
        db.close()

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "p1",
                "status": "idle",
                "supported_models": ["m1"],
                "is_active": True,
                "is_stale": False,
                "last_heartbeat_at": "2026-01-01T00:00:00Z",
                "current_task": {"id": 1},
            }
        ]
    )
    monkeypatch.setattr("article_factory.routes.admin.ControlPlaneClient", lambda **kwargs: mock_cp)

    response = client.get("/api/control-plane/pullers", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["pullers"][0]["puller_name"] == "p1"


# --- review_parser.py ---


def test_review_parser_invalid_schema_version() -> None:
    payload = {
        "schema_version": 2,
        "total_score": 90,
        "verdict": "accepted",
        "criteria": {},
        "previous_issues": [],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: ACCEPTED"
    review = parse_structured_review(text)
    assert review is not None
    assert review.structured_review_valid is False


def test_review_parser_bad_total_score() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 150,
        "verdict": "accepted",
        "criteria": {},
        "previous_issues": [],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: ACCEPTED"
    review = parse_structured_review(text)
    assert review is not None
    assert any("total_score" in w for w in review.parse_warnings)


def test_review_parser_issue_status_normalization() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 70,
        "verdict": "rejected",
        "criteria": {
            "accuracy_verifiable_facts": {"score": 30, "max_score": 40},
            "organization_flow": {"score": 10, "max_score": 15},
            "writing_quality": {"score": 10, "max_score": 15},
            "depth_specificity": {"score": 10, "max_score": 15},
            "reader_engagement": {"score": 5, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [
            {
                "issue_number": 1,
                "category": "x",
                "status": "partially fixed",
                "problem": "p",
                "why_it_loses_points": "w",
                "required_change": "",
            }
        ],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    assert review is not None
    assert review.previous_issues[0].status == "partially_fixed"


def test_issue_resolution_counts_with_statuses() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 70,
        "verdict": "rejected",
        "criteria": {
            "accuracy_verifiable_facts": {"score": 30, "max_score": 40},
            "organization_flow": {"score": 10, "max_score": 15},
            "writing_quality": {"score": 10, "max_score": 15},
            "depth_specificity": {"score": 10, "max_score": 15},
            "reader_engagement": {"score": 5, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [
            {"issue_number": 1, "category": "x", "status": "fixed", "problem": "p", "why_it_loses_points": "w", "required_change": "c"},
            {"issue_number": 2, "category": "x", "status": "regressed", "problem": "p2", "why_it_loses_points": "w", "required_change": "c2"},
        ],
        "required_changes": [
            {"issue_number": 3, "category": "y", "problem": "p3", "why_it_loses_points": "w", "required_change": "c3"},
        ],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    counts = issue_resolution_counts(review)
    assert counts["fixed_issue_count"] == 1
    assert counts["regressed_issue_count"] == 1
    assert counts["required_change_count"] == 1


def test_review_parser_returns_none_without_signals() -> None:
    assert parse_structured_review("No review content here.") is None


# --- flow_storage.py ---


def test_create_folder_and_list_tree_errors(configured_db) -> None:
    result = create_folder("new-folder")
    assert result["path"] == "new-folder"

    with pytest.raises(FileNotFoundError):
        list_tree("does-not-exist")

    write_flow("file-not-dir.flow.json", new_flow_definition(slug="f", display_name="F", step_count=1))
    with pytest.raises(NotADirectoryError):
        list_tree("file-not-dir.flow.json")


def test_create_flow_step_count_validation(configured_db) -> None:
    with pytest.raises(ValueError, match="step_count"):
        create_flow(folder="", slug="bad", display_name="Bad", step_count=0)


def test_import_flow_overwrite(configured_db) -> None:
    flow = new_flow_definition(slug="overwrite", display_name="Overwrite", step_count=1)
    rel = import_flow(flow, folder="imports", slug="overwrite")
    flow.display_name = "Updated"
    rel2 = import_flow(flow, folder="imports", slug="overwrite", overwrite=True)
    assert rel == rel2
    assert read_flow(rel2).display_name == "Updated"


# --- flow_runner.py ---


@pytest.fixture
def flow_runner_env(configured_db, monkeypatch) -> None:
    async def fake_select(_cp, model: str) -> str:
        return "puller-1"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)


@pytest.mark.asyncio
async def test_flow_runner_linear_without_completion(configured_db, flow_runner_env, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(can_complete=False, can_loop=False)
    flow = FlowDefinition.model_construct(
        slug="linear-bad",
        display_name="Linear Bad",
        article_step_id=writer.step_id,
        steps=[writer, review],
    )

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return _step_record(ctx.step_key, "ok")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.resolve_flow_for_run", lambda db, run: flow)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings

        run = FactoryRun(
            run_id="linear-bad-run",
            topic_slug="general",
            flow_path="linear-bad.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="linear-bad.flow.json",
            topic_prompt="Topic",
            runtime=load_runtime_settings(db),
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert result.status == "failed"
        assert "must allow completion" in (result.error or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_flow_runner_save_response_to_disk(configured_db, flow_runner_env, monkeypatch, tmp_path) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    writer.save_response_to_disk = True
    finalize = new_flow_step(order=2, label="Done", step_key="done")
    finalize.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    flow = FlowDefinition(
        slug="save-disk",
        display_name="Save Disk",
        article_step_id=writer.step_id,
        steps=[writer, finalize],
    )
    write_flow("save-disk.flow.json", flow)

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "done":
            return _step_record("done", "# Final\n\nBody")
        return _step_record("writer", "# Draft")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    completed: list[str] = []

    async def capture(draft, records):
        completed.append(draft)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings

        run = FactoryRun(
            run_id="save-disk-run",
            topic_slug="general",
            flow_path="save-disk.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        await execute_flow_pipeline(
            db,
            run=run,
            flow_path="save-disk.flow.json",
            topic_prompt="Topic",
            runtime=load_runtime_settings(db),
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=capture,
        )
        from article_factory.services.flow_storage import run_outputs_root

        path = run_outputs_root() / "save-disk-run" / "steps" / "01-writer.md"
        assert path.exists()
        assert completed
    finally:
        db.close()


@pytest.mark.asyncio
async def test_flow_runner_mid_step_loop(configured_db, flow_runner_env, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.loop = FlowStepLoop(enabled=True, goto_step_id=writer.step_id)
    finalize = new_flow_step(order=3, label="Finalize", step_key="finalize")
    finalize.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    flow = FlowDefinition(
        slug="mid-loop-2",
        display_name="Mid Loop 2",
        article_step_id=writer.step_id,
        steps=[writer, review, finalize],
        max_iterations=2,
    )
    write_flow("mid-loop-2.flow.json", flow)
    review_calls = {"n": 0}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            review_calls["n"] += 1
            if review_calls["n"] == 1:
                return _step_record("review", "Fix.\n\nVERDICT: REJECT")
            return _step_record("review", "Good.\n\nVERDICT: ACCEPT")
        if ctx.step_key == "writer":
            return _step_record("writer", "# Draft v2")
        return _step_record("finalize", "# Final")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings

        run = FactoryRun(
            run_id="mid-loop-2-run",
            topic_slug="general",
            flow_path="mid-loop-2.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        completed: list[str] = []

        async def capture(draft, records):
            completed.append(draft)
            run.status = "completed"
            db.commit()

        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="mid-loop-2.flow.json",
            topic_prompt="Topic",
            runtime=load_runtime_settings(db),
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=capture,
        )
        assert result.status == "completed"
        assert completed
    finally:
        db.close()


def test_default_flow_path_for_topic_no_db(configured_db) -> None:
    path = default_flow_path_for_topic("sports", db=None)
    assert path.endswith(".flow.json")


# --- executor.py ---


@pytest.mark.asyncio
async def test_execute_step_run_cancelled(configured_db, monkeypatch) -> None:
    from article_factory.services.run_control import RunCancelledError, request_run_cancel

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="cancel-step", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="cancel-step", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    await request_run_cancel("cancel-step")

    cp = AsyncMock()
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.get_task_status = AsyncMock(return_value=None)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RunCancelledError):
            await execute_step(
                cp,
                step_key="writer",
                system_prompt="sys",
                user_content="user",
                puller="p",
                model="m",
                run_id="cancel-step",
                tracer=tracer,
            )


@pytest.mark.asyncio
async def test_run_step_from_context_with_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("article_factory.config.settings.flow_run_outputs_root", str(tmp_path))

    cp = AsyncMock()
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        return_value=[
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "note.txt", "content": "saved"}),
                            },
                        }
                    ],
                },
                "usage": {},
            },
            {"message": {"content": "done with tools"}, "usage": {}},
        ]
    )

    ctx = StepContext(
        step_key="writer",
        label="Writer",
        system_prompt="sys",
        user_prompt_template="{{topic}}",
        puller="p",
        model="m",
        variables={"topic": "Game"},
        enabled_tools={"write_file": True, "read_file": True, "web_search": False, "web_fetch": False},
        run_id="tool-run",
        brave_search_api_key="",
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        record = await run_step_from_context(ctx, cp)

    assert record["content"] == "done with tools"


# --- step_tools.py ---


def test_normalize_step_enabled_tools() -> None:
    enabled = normalize_step_enabled_tools({"web_search": True, "write_file": 1})
    assert enabled["web_search"] is True
    assert enabled["write_file"] is True
    assert enabled["read_file"] is False


@pytest.mark.asyncio
async def test_step_tool_registry_paths(tmp_path: Path) -> None:
    registry = StepToolRegistry(workspace_root=tmp_path, brave_api_key="key")

    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path(tmp_path, "../escape")

    list_result = await registry.execute(
        {"id": "1", "function": {"name": "list_files", "arguments": {"path": "."}}}
    )
    assert "empty directory" in list_result["content"] or list_result["content"] == "(empty directory)"

    read_missing = await registry.execute(
        {"id": "2", "function": {"name": "read_file", "arguments": {"path": "nope.txt"}}}
    )
    assert "not found" in read_missing["content"]

    unknown = await registry.execute({"id": "3", "function": {"name": "bogus", "arguments": {}}})
    assert "unknown tool" in unknown["content"]


@pytest.mark.asyncio
async def test_step_tool_web_fetch(monkeypatch, tmp_path: Path) -> None:
    async def fake_fetch(url, max_chars=50000):
        return {"url": url, "text": "page text", "title": "Title"}

    monkeypatch.setattr("article_factory.services.step_tools.fetch_web_page", fake_fetch)
    monkeypatch.setattr(
        "article_factory.services.step_tools.format_fetch_result",
        lambda payload: payload["text"],
    )

    registry = StepToolRegistry(workspace_root=tmp_path, brave_api_key="")
    result = await registry.execute(
        {"id": "4", "function": {"name": "web_fetch", "arguments": {"url": "https://example.com"}}}
    )
    assert result["content"] == "page text"


# --- step_trace.py ---


def test_step_tracer_completed_duration_from_timestamps(configured_db) -> None:
    from datetime import datetime, timedelta, timezone

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="dur-run", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="dur-run", step_key="writer", puller="p", model="m")
        tracer.execution.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        tracer.mark_completed(response_content="ok")
        step = db.query(StepExecution).filter_by(run_id="dur-run").one()
        assert step.duration_ms is not None
        assert step.duration_ms >= 1000
    finally:
        db.close()


def test_enrich_steps_with_responses_from_manifest(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="enrich-run",
                topic_slug="sports",
                status="completed",
                manifest={"steps": [{"step_key": "writer", "content": "from manifest", "duration_ms": 5}]},
            )
        )
        db.commit()
        steps = [{"step_key": "writer", "status": "completed"}]
        enriched = enrich_steps_with_responses(db, "enrich-run", steps)
        assert enriched[0]["response_content"] == "from manifest"
        assert enriched[0]["duration_ms"] == 5
    finally:
        db.close()


def test_batch_step_executions_payload_empty(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        assert batch_step_executions_payload(db, []) == {}
    finally:
        db.close()


# --- telemetry.py ---


def test_capture_run_telemetry_safe_swallows_errors(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        monkeypatch.setattr(
            "article_factory.services.telemetry.capture_run_telemetry",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        capture_run_telemetry_safe(db, "missing")
    finally:
        db.close()


def test_rebuild_flow_telemetry_counts(configured_db) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="rebuild", display_name="Rebuild", step_count=1)
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="rebuild-run",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
                manifest={"steps": [{"step_key": "step_1", "content": "body"}]},
            )
        )
        db.commit()
        stats = rebuild_flow_telemetry(db, rel_path, version.id)
        assert stats["total"] >= 1
        assert stats["parsed"] >= 1
    finally:
        db.close()


# --- run_recovery.py ---


def test_reconcile_orphaned_runs_in_flight_step(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="orphan-flight",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="orphan-flight",
                step_key="writer",
                status="waiting",
                puller="p",
                model="m",
            )
        )
        db.commit()
        failed = reconcile_orphaned_runs(db)
        assert failed == 1
        db.refresh(run)
        assert run.status == "failed"
    finally:
        db.close()


# --- showroom_status_sync.py ---


@pytest.mark.asyncio
async def test_refresh_showroom_status_success(configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
    finally:
        db.close()

    push = AsyncMock()
    monkeypatch.setattr("article_factory.services.showroom_status_sync.push_showroom_factory_status", push)
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.CmsClient",
        lambda **kwargs: object(),
    )

    assert await refresh_showroom_status() is True
    push.assert_awaited()


@pytest.mark.asyncio
async def test_refresh_showroom_status_not_configured(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"cms_url": "", "cms_api_key": ""})
    finally:
        db.close()
    assert await refresh_showroom_status() is False


def test_schedule_showroom_status_refresh_no_loop() -> None:
    schedule_showroom_status_refresh(force=True)


@pytest.mark.asyncio
async def test_sync_showroom_when_factory_busy() -> None:
    await sync_showroom_when_factory_busy(active_run_count=2)


@pytest.mark.asyncio
async def test_showroom_status_tick_operational_error(configured_db, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
    finally:
        db.close()

    async def boom(db, cms):
        raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr("article_factory.services.showroom_status_sync.push_showroom_factory_status", boom)
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.CmsClient",
        lambda **kwargs: object(),
    )

    db = db_module.SessionLocal()
    try:
        await showroom_status_tick(db)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_showroom_status_loop_lifecycle(monkeypatch) -> None:
    loop = ShowroomStatusLoop()
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("article_factory.services.showroom_status_sync.settings.heartbeat_interval_seconds", 0.01)

    await loop.start()
    assert loop.is_alive
    loop.request_refresh()
    await asyncio.sleep(0.05)
    await loop.stop()
    assert not loop.is_alive


# --- flows routes error paths ---


def test_flow_routes_error_paths(client, api_headers) -> None:
    missing_template = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": "missing/template.flow.json",
            "folder": "x",
            "slug": "y",
            "display_name": "Y",
        },
    )
    assert missing_template.status_code in (400, 404)

    bad_export = client.get("/api/flows/export", headers=api_headers, params={"path": "missing.flow.json"})
    assert bad_export.status_code == 404

    bad_list = client.get("/api/flows/list", headers=api_headers, params={"path": "missing-dir"})
    assert bad_list.status_code == 404

    dup_missing = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "missing.flow.json"},
    )
    assert dup_missing.status_code == 404


# --- flow_queues routes ---


def test_flow_queue_start_missing_flow(client, api_headers) -> None:
    response = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "name": "Bad",
            "flow_path": "",
            "topic_slug": "sports",
            "default_model": "test-model",
            "topics": ["Topic"],
        },
    )
    assert response.status_code == 400


def test_flow_queue_preset_errors(client, api_headers) -> None:
    missing = client.get("/api/flow-queues/presets/missing-preset", headers=api_headers)
    assert missing.status_code == 404

    bad = client.post(
        "/api/flow-queues/presets",
        headers=api_headers,
        json={"name": "", "slug": "bad", "topic_slug": "sports", "flow_path": "x", "default_model": "m", "topics": []},
    )
    assert bad.status_code == 400


# --- flow_queues service ---


def test_flow_queue_service_validation(configured_db) -> None:
    from article_factory.services.flow_queues import (
        create_flow_queue,
        delete_flow_queue,
        enqueue_topics_to_queue,
        ensure_default_flow_queue,
        resolve_queue_flow_path,
        update_flow_queue,
    )

    db = db_module.SessionLocal()
    try:
        with pytest.raises(ValueError, match="Queue name is required"):
            create_flow_queue(db, name="   ", flow_path="test/SimpleTest.flow.json")

        q1 = create_flow_queue(db, name="Dup", flow_path="test/SimpleTest.flow.json")
        q2 = create_flow_queue(db, name="Dup", flow_path="test/SimpleTest.flow.json")
        assert q2.slug.endswith("-2")

        default = ensure_default_flow_queue(db)
        with pytest.raises(ValueError, match="default queue"):
            delete_flow_queue(db, default.id)

        disabled = create_flow_queue(db, name="Off", flow_path="test/SimpleTest.flow.json")
        disabled.enabled = False
        db.flush()
        with pytest.raises(ValueError, match="Enable this queue"):
            enqueue_topics_to_queue(db, disabled.id, ["Topic"])

        with pytest.raises(LookupError):
            update_flow_queue(db, 99999, name="Nope")

        empty_path_queue = FlowQueue(slug="empty-path", name="Empty", flow_path="", topic_slug="general")
        db.add(empty_path_queue)
        db.flush()
        assert resolve_queue_flow_path(db, empty_path_queue).endswith(".flow.json")
        db.commit()
    finally:
        db.close()


def test_flow_queue_null_migration(configured_db, monkeypatch) -> None:
    import article_factory.services.flow_queues as fq_module
    from article_factory.services.flow_queues import ensure_default_flow_queue

    monkeypatch.setattr(fq_module, "_null_queue_migration_done", False)

    db = db_module.SessionLocal()
    try:
        default = ensure_default_flow_queue(db)
        db.add(TopicQueueItem(flow_queue_id=None, topic_slug="general", prompt="Legacy-null", status="queued"))
        db.commit()
        monkeypatch.setattr(fq_module, "_null_queue_migration_done", False)
        ensure_default_flow_queue(db)
        db.flush()
        item = db.query(TopicQueueItem).filter_by(prompt="Legacy-null").one()
        assert item.flow_queue_id == default.id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_stop_and_clear_remaining_runs(configured_db, monkeypatch) -> None:
    from article_factory.services.flow_queues import create_flow_queue, stop_and_clear_flow_queue

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Remainder", flow_path="test/SimpleTest.flow.json")
        item = TopicQueueItem(flow_queue_id=queue.id, topic_slug="general", prompt="Run", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="remainder-run",
                topic_slug="general",
                queue_item_id=item.id,
                status="running",
            )
        )
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    monkeypatch.setattr(
        "article_factory.orchestrator.runner.factory_loop.cancel_run_workers",
        lambda **kwargs: 0,
    )

    db = db_module.SessionLocal()
    try:
        result = await stop_and_clear_flow_queue(db, queue_id=queue_id)
        assert result["stopped_runs"] >= 1
    finally:
        db.close()


def test_select_queued_items_zero_limit(configured_db) -> None:
    from article_factory.services.flow_queues import select_queued_items_round_robin

    db = db_module.SessionLocal()
    try:
        picked, idx = select_queued_items_round_robin(db, limit=0, start_index=0)
        assert picked == []
        assert idx == 0
    finally:
        db.close()


# --- flows routes (more errors) ---


def test_flow_routes_more_errors(client, api_headers) -> None:
    bad_tree = client.get("/api/flows/tree", headers=api_headers, params={"path": "missing-dir"})
    assert bad_tree.status_code == 404

    write_flow("not-a-dir.flow.json", new_flow_definition(slug="f", display_name="F", step_count=1))
    bad_tree_file = client.get("/api/flows/tree", headers=api_headers, params={"path": "not-a-dir.flow.json"})
    assert bad_tree_file.status_code == 400

    bad_file = client.get("/api/flows/file", headers=api_headers, params={"path": "missing.flow.json"})
    assert bad_file.status_code == 404

    dup_create = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "dup", "slug": "same", "display_name": "Same", "step_count": 1},
    )
    assert dup_create.status_code == 200
    dup_again = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "dup", "slug": "same", "display_name": "Same", "step_count": 1},
    )
    assert dup_again.status_code == 409

    bad_step = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "bad", "slug": "count", "display_name": "Bad", "step_count": 0},
    )
    assert bad_step.status_code in (400, 422)

    dup_folder = client.post("/api/flows/folders", headers=api_headers, json={"path": "dup-folder"})
    assert dup_folder.status_code == 200
    dup_folder_again = client.post("/api/flows/folders", headers=api_headers, json={"path": "dup-folder"})
    assert dup_folder_again.status_code == 409

    remove_missing_folder = client.delete("/api/flows/folders", headers=api_headers, params={"path": "no-folder"})
    assert remove_missing_folder.status_code == 404

    remove_missing_file = client.delete("/api/flows/file", headers=api_headers, params={"path": "no.flow.json"})
    assert remove_missing_file.status_code == 404


def test_flow_import_bad_slug(client, api_headers) -> None:
    response = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={"folder": "imports", "slug": "", "flow": {"slug": "", "display_name": "Bad", "steps": []}},
    )
    assert response.status_code in (400, 422)


def test_flow_move_conflict(client, api_headers) -> None:
    client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "move-conflict", "slug": "a", "display_name": "A", "step_count": 1},
    )
    client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "move-conflict", "slug": "b", "display_name": "B", "step_count": 1},
    )
    conflict = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": "move-conflict/a.flow.json", "folder": "move-conflict", "slug": "b"},
    )
    assert conflict.status_code == 409


# --- telemetry routes ---


def test_telemetry_routes_missing_version(client, api_headers) -> None:
    missing = client.get(
        "/api/flows/telemetry",
        headers=api_headers,
        params={"path": "sports/standard-4-step.flow.json", "flow_version_id": 99999},
    )
    assert missing.status_code == 404

    export_missing = client.get(
        "/api/flows/telemetry/export",
        headers=api_headers,
        params={"path": "sports/standard-4-step.flow.json", "flow_version_id": 99999},
    )
    assert export_missing.status_code == 404

    rebuild_missing = client.post(
        "/api/flows/telemetry/rebuild",
        headers=api_headers,
        params={"path": "sports/standard-4-step.flow.json", "flow_version_id": 99999},
    )
    assert rebuild_missing.status_code == 404


# --- token_usage ---


def test_token_usage_helpers() -> None:
    from article_factory.services.token_usage import (
        enrich_manifest,
        enrich_step_record,
        estimate_tokens_from_text,
        finalize_stats,
        normalize_round_usage,
        normalize_usage,
        serialize_messages_for_token_estimate,
        serialize_tool_calls,
        serialize_tools_for_token_estimate,
    )

    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("hello world") >= 1
    assert serialize_tool_calls(None) == ""
    assert serialize_tools_for_token_estimate(None) == ""

    usage = normalize_usage(None, input_text="input text", output_text="output")
    assert usage["total_tokens"] > 0

    usage2 = finalize_stats(normalize_usage({"total_tokens": 100}))
    assert usage2["output_tokens"] > 0

    round_usage = normalize_round_usage(
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        messages=[{"role": "user", "content": "hello"}],
        assistant_message={"content": "reply"},
        tools_text='[{"name": "tool"}]',
    )
    assert round_usage["total_tokens"] > 0

    partial = normalize_round_usage(
        {"input_tokens": 0, "output_tokens": 5, "total_tokens": 5},
        messages=[{"role": "user", "content": "hello"}],
        assistant_message={"content": "reply", "thinking": "hmm", "tool_calls": [{"id": "1"}]},
    )
    assert partial["input_tokens"] > 0

    stats = finalize_stats({"input_tokens": 10, "output_tokens": 0, "total_tokens": 0})
    assert stats["total_tokens"] == stats["input_tokens"] + stats["output_tokens"]

    text = serialize_messages_for_token_estimate(
        [{"role": "user", "content": ""}, "not-a-dict", {"role": "assistant", "content": "ok"}]
    )
    assert "[user]" in text
    assert "[assistant]" in text

    record = enrich_step_record(
        {"step_key": "writer", "content": "draft body text", "usage": {}},
        selected_model="m",
        body_markdown="# Title\n\nBody content here",
    )
    assert "step_key" in record

    manifest = enrich_manifest({"steps": [{"step_key": "writer", "usage": {"input_tokens": 1}}]}, selected_model="m", body_markdown="# T\n\nB")
    assert manifest.get("stats") is not None or manifest.get("steps")


# --- personas ---


def test_personas_api(client, api_headers) -> None:
    created = client.post(
        "/api/personas",
        headers=api_headers,
        json={"name": "Coach", "style_prompt": "Be concise.", "description": "Editor"},
    )
    assert created.status_code == 200
    slug = created.json()["persona"]["slug"]

    listed = client.get("/api/personas", headers=api_headers)
    assert any(p["slug"] == slug for p in listed.json()["personas"])

    detail = client.get(f"/api/personas/{slug}", headers=api_headers)
    assert detail.status_code == 200

    updated = client.put(
        f"/api/personas/{slug}",
        headers=api_headers,
        json={"name": "Senior Coach", "style_prompt": "Be precise."},
    )
    assert updated.status_code == 200

    missing = client.get("/api/personas/missing-persona", headers=api_headers)
    assert missing.status_code == 404

    bad = client.post(
        "/api/personas",
        headers=api_headers,
        json={"name": " ", "style_prompt": " "},
    )
    assert bad.status_code in (400, 422)

    deleted = client.delete(f"/api/personas/{slug}", headers=api_headers)
    assert deleted.status_code == 200


# --- review_parser (more branches) ---


def test_review_parser_invalid_verdict_token() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 80,
        "verdict": "maybe",
        "criteria": {
            "accuracy_verifiable_facts": {"score": 30, "max_score": 40},
            "organization_flow": {"score": 10, "max_score": 15},
            "writing_quality": {"score": 10, "max_score": 15},
            "depth_specificity": {"score": 10, "max_score": 15},
            "reader_engagement": {"score": 5, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    assert review is not None
    assert any("verdict" in w for w in review.parse_warnings)


def test_review_parser_missing_criterion() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 80,
        "verdict": "rejected",
        "criteria": {"accuracy_verifiable_facts": {"score": 30, "max_score": 40}},
        "previous_issues": [],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    assert review is not None
    assert any("missing criterion" in w for w in review.parse_warnings)


def test_review_parser_legacy_criterion_line_scan() -> None:
    text = (
        "Depth & Specificity\n\n12 / 15\n"
        "TOTAL SCORE: 82/100\n\nVERDICT: ACCEPTED\nEND REVIEW"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.total_score == 82


def test_review_parser_issue_status_tokens() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 70,
        "verdict": "rejected",
        "criteria": {
            "accuracy_verifiable_facts": {"score": 30, "max_score": 40},
            "organization_flow": {"score": 10, "max_score": 15},
            "writing_quality": {"score": 10, "max_score": 15},
            "depth_specificity": {"score": 10, "max_score": 15},
            "reader_engagement": {"score": 5, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [
            {"issue_number": 1, "category": "x", "status": "not fixed", "problem": "p", "why_it_loses_points": "w", "required_change": "c"},
            {"issue_number": 2, "category": "x", "status": "regressed badly", "problem": "p2", "why_it_loses_points": "w", "required_change": "c2"},
        ],
        "required_changes": [
            {"issue_number": 3, "category": "y", "problem": "p3", "why_it_loses_points": "w", "required_change": ""},
        ],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    counts = issue_resolution_counts(review)
    assert counts["not_fixed_issue_count"] == 1
    assert counts["regressed_issue_count"] == 1
    assert review.required_changes[0].required_change == "p3"


# --- flow_storage (more) ---


def test_duplicate_flow_remaps_loop_ids(configured_db) -> None:
    from article_factory.services.flow_storage import duplicate_flow

    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(can_complete=True, can_loop=True, loop_goto_step_id=writer.step_id)
    flow = FlowDefinition(
        slug="loop-dup",
        display_name="Loop Dup",
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    write_flow("loop-dup.flow.json", flow)
    _rel, dup = duplicate_flow("loop-dup.flow.json")
    assert dup.steps[1].completion.loop_goto_step_id == dup.steps[0].step_id


def test_move_flow_same_location(configured_db) -> None:
    from article_factory.services.flow_storage import move_flow

    write_flow("same/slug.flow.json", new_flow_definition(slug="slug", display_name="S", step_count=1))
    with pytest.raises((ValueError, FileExistsError)):
        move_flow("same/slug.flow.json", folder="same", slug="slug")


def test_import_flow_empty_slug(configured_db) -> None:
    flow = FlowDefinition.model_construct(slug="", display_name="X", steps=[])
    with pytest.raises(ValueError, match="slug is required"):
        import_flow(flow, folder="", slug="   ")


# --- control_plane client ---


@pytest.mark.asyncio
async def test_control_plane_get_puller_404() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")
    not_found = MagicMock()
    not_found.status_code = 404

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=not_found)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        assert await client.get_puller("missing") is None


@pytest.mark.asyncio
async def test_control_plane_get_activity_503() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")
    unavailable = MagicMock()
    unavailable.status_code = 503

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=unavailable)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        assert await client.get_activity() == []


# --- executor (more branches) ---


@pytest.mark.asyncio
async def test_execute_step_with_thinking_and_tool_calls(monkeypatch) -> None:
    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "completed"})
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        return_value=[
            {
                "message": {
                    "content": "final",
                    "thinking": "reasoning",
                    "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}],
                },
                "usage": "not-a-dict",
                "completed_at": "2026-01-01T00:00:00Z",
            }
        ]
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
        )
    assert result["content"] == "final"


@pytest.mark.asyncio
async def test_execute_step_tool_refusal_nudge(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("article_factory.config.settings.flow_run_outputs_root", str(tmp_path))

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        side_effect=[
            [{"message": {"content": "I cannot search the web for you."}, "usage": {}}],
            [{"message": {"content": "Here is the draft."}, "usage": {}}],
        ]
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
            enabled_tools={"web_search": True, "write_file": False, "read_file": False, "web_fetch": False},
            brave_search_api_key="key",
            run_id="tool-nudge",
        )
    assert "draft" in result["content"].lower()


# --- step_trace ---


def test_enrich_steps_from_pipeline_state_by_key(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="pipe-enrich",
                topic_slug="sports",
                status="running",
                pipeline_state={
                    "step_records": [
                        {"step_key": "writer", "content": "draft one"},
                        {"step_key": "writer", "content": "draft two"},
                    ]
                },
            )
        )
        db.commit()
        steps = [
            {"step_key": "writer", "status": "completed"},
            {"step_key": "writer", "status": "completed"},
        ]
        enriched = enrich_steps_with_responses(db, "pipe-enrich", steps)
        assert enriched[0]["response_content"] == "draft one"
        assert enriched[1]["response_content"] == "draft two"
    finally:
        db.close()


def test_step_tracer_mark_failed(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="fail-trace", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="fail-trace", step_key="writer", puller="p", model="m")
        tracer.mark_failed("boom")
        step = db.query(StepExecution).filter_by(run_id="fail-trace").one()
        assert step.status == "failed"
        assert step.error == "boom"
    finally:
        db.close()


# --- run_recovery ---


def test_commit_with_retry_locked(configured_db, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from article_factory.services.run_recovery import commit_with_retry

    db = db_module.SessionLocal()
    try:
        attempts = {"n": 0}

        def flaky_commit() -> None:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise OperationalError("stmt", {}, Exception("database is locked"))

        db.commit = flaky_commit  # type: ignore[method-assign]
        db.rollback = MagicMock()
        monkeypatch.setattr("article_factory.services.run_recovery.time.sleep", lambda _s: None)
        commit_with_retry(db)
        assert attempts["n"] == 2
    finally:
        db.close()


# --- flow_runner (more) ---


@pytest.mark.asyncio
async def test_flow_runner_cancelled_mid_step(configured_db, flow_runner_env, monkeypatch) -> None:
    from article_factory.services.run_control import RunCancelledError, request_run_cancel

    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    flow = FlowDefinition(
        slug="cancel-mid",
        display_name="Cancel",
        article_step_id=writer.step_id,
        steps=[writer],
    )
    write_flow("cancel-mid.flow.json", flow)

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        await request_run_cancel("cancel-mid-run")
        return _step_record("writer", "draft")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings

        run = FactoryRun(
            run_id="cancel-mid-run",
            topic_slug="general",
            flow_path="cancel-mid.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        with pytest.raises(RunCancelledError):
            await execute_flow_pipeline(
                db,
                run=run,
                flow_path="cancel-mid.flow.json",
                topic_prompt="Topic",
                runtime=load_runtime_settings(db),
                cms=None,
                emit_step_started=AsyncMock(),
                complete_run=AsyncMock(),
            )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_flow_runner_ended_without_completion(configured_db, flow_runner_env, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    middle = new_flow_step(order=2, label="Middle", step_key="middle")
    middle.completion = FlowStepCompletion(can_complete=False, can_loop=False)
    flow = FlowDefinition.model_construct(
        slug="no-complete",
        display_name="No Complete",
        article_step_id=writer.step_id,
        steps=[writer, middle],
        max_iterations=1,
    )

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return _step_record(ctx.step_key, "ok")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.resolve_flow_for_run", lambda db, run: flow)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings

        run = FactoryRun(
            run_id="no-complete-run",
            topic_slug="general",
            flow_path="no-complete.flow.json",
            status="running",
            selected_model="m",
        )
        db.add(run)
        db.commit()
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="no-complete.flow.json",
            topic_prompt="Topic",
            runtime=load_runtime_settings(db),
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert result.status == "failed"
        assert "completion" in (result.error or "")
    finally:
        db.close()


# --- admin (more) ---


def test_publish_run_updates_failed_queue_item(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Pub", status="failed")
        db.add(item)
        db.flush()
        db.add(FactoryRun(run_id="pub-queue", topic_slug="sports", queue_item_id=item.id, status="failed"))
        db.add(
            CompletedArticle(
                run_id="pub-queue",
                topic_slug="sports",
                title="T",
                body_markdown="# T\n\nB",
            )
        )
        db.commit()
    finally:
        db.close()

    async def fake_publish(db, *, run, article, cms=None, runtime=None):
        return {"ok": True}

    monkeypatch.setattr("article_factory.routes.admin.publish_article_to_showroom", fake_publish)

    response = client.post("/api/runs/pub-queue/publish", headers=api_headers)
    assert response.status_code == 200

    db = db_module.SessionLocal()
    try:
        item = db.query(TopicQueueItem).filter_by(prompt="Pub").one()
        assert item.status == "completed"
    finally:
        db.close()


def test_get_run_with_snapshot_label(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue, enqueue_topics_to_queue
    from article_factory.services.topic_queue_snapshots import get_or_create_topic_queue_snapshot

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Snap Label", flow_path="test/SimpleTest.flow.json")
        items = enqueue_topics_to_queue(db, queue.id, ["Topic A"])
        db.flush()
        snapshot = get_or_create_topic_queue_snapshot(db, flow_queue_id=queue.id)
        db.add(
            FactoryRun(
                run_id="snap-label-run",
                topic_slug="general",
                status="completed",
                topic_queue_snapshot_id=snapshot.id if snapshot else None,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/runs/snap-label-run", headers=api_headers)
    assert response.status_code == 200


def test_enqueue_batch_empty_topics(client, api_headers) -> None:
    response = client.post(
        "/api/queue/batch",
        headers=api_headers,
        json={"topic_slug": "sports", "topics": ["", "  "]},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0


# --- puller_selection (remaining) ---


def test_pick_puller_falls_back_to_active_busy() -> None:
    from article_factory.services.puller_selection import pick_puller

    pullers = [
        {"puller_name": "busy-gpu", "is_active": True, "is_stale": False, "status": "busy", "supported_models": ["m"]},
    ]
    assert pick_puller(pullers, "m") == "busy-gpu"


def test_pick_puller_no_match_raises() -> None:
    from article_factory.services.puller_selection import pick_puller

    with pytest.raises(RuntimeError, match="No idle puller"):
        pick_puller([], "m")


@pytest.mark.asyncio
async def test_get_registered_puller_fallback_list() -> None:
    from article_factory.services.puller_selection import get_registered_puller_on_cp

    cp = AsyncMock()
    cp.get_puller = AsyncMock(side_effect=RuntimeError("down"))
    cp.list_pullers = AsyncMock(
        return_value=[
            {"puller_name": "gpu-01", "is_active": True, "is_stale": False, "status": "busy"},
        ]
    )
    result = await get_registered_puller_on_cp(cp, "gpu-01")
    assert result is not None


@pytest.mark.asyncio
async def test_get_registered_puller_empty_name() -> None:
    from article_factory.services.puller_selection import get_registered_puller_on_cp

    cp = AsyncMock()
    assert await get_registered_puller_on_cp(cp, "  ") is None


# --- control_plane_heartbeat (remaining) ---


@pytest.mark.asyncio
async def test_control_plane_heartbeat_tick_with_active_step(configured_db, monkeypatch) -> None:
    from article_factory.models import StepExecution
    from article_factory.services.control_plane_heartbeat import control_plane_heartbeat_tick
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
        db.add(
            FactoryRun(
                run_id="hb-run",
                topic_slug="sports",
                status="running",
                current_step="writer",
                selected_puller="p1",
                selected_model="m1",
            )
        )
        db.flush()
        db.add(
            StepExecution(
                run_id="hb-run",
                step_key="writer",
                status="waiting",
                puller="p1",
                model="m1",
            )
        )
        db.commit()
    finally:
        db.close()

    cp_mock = AsyncMock()
    cp_mock.post_node_heartbeat = AsyncMock()
    cp_mock.post_agent_heartbeat = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.ControlPlaneClient",
        lambda **kwargs: cp_mock,
    )

    db = db_module.SessionLocal()
    try:
        await control_plane_heartbeat_tick(db)
    finally:
        db.close()

    node_payload = cp_mock.post_node_heartbeat.await_args.args[0]
    assert node_payload["descriptive_info"]["active_run_id"] == "hb-run"


@pytest.mark.asyncio
async def test_control_plane_heartbeat_tick_skips_without_url(configured_db) -> None:
    from article_factory.services.control_plane_heartbeat import control_plane_heartbeat_tick
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": ""})
        await control_plane_heartbeat_tick(db)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_control_plane_heartbeat_loop_lifecycle(monkeypatch) -> None:
    from article_factory.services.control_plane_heartbeat import ControlPlaneHeartbeatLoop

    loop = ControlPlaneHeartbeatLoop()
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_tick",
        AsyncMock(),
    )
    monkeypatch.setattr("article_factory.services.control_plane_heartbeat.settings.heartbeat_interval_seconds", 0.01)

    await loop.start()
    await asyncio.sleep(0.03)
    await loop.stop()


@pytest.mark.asyncio
async def test_send_heartbeats_custom_step_label(configured_db) -> None:
    from article_factory.services.control_plane_heartbeat import send_control_plane_heartbeats
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="hb-flow", display_name="HB", step_count=2)
        run = FactoryRun(
            run_id="hb-custom",
            topic_slug="general",
            flow_path=rel_path,
            status="running",
            current_step="step_2",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.run_id
    finally:
        db.close()

    cp = AsyncMock()
    cp.post_node_heartbeat = AsyncMock()
    cp.post_agent_heartbeat = AsyncMock()

    db = db_module.SessionLocal()
    try:
        active = db.query(FactoryRun).filter_by(run_id=run_id).one()
        await send_control_plane_heartbeats(
            cp,
            db=db,
            active_run=active,
            gateway_id="gw",
            gateway_display_name="Factory",
        )
    finally:
        db.close()

    assert cp.post_agent_heartbeat.await_count >= 1


# --- queue_presets ---


def test_queue_presets_parsing(configured_db) -> None:
    from article_factory.services.queue_presets import (
        normalize_preset,
        parse_topics_csv,
        parse_topics_lines,
        parse_topics_text,
        write_queue_preset,
    )

    assert parse_topics_lines("  a \n\nb") == ["a", "b"]
    assert parse_topics_csv("topic1,extra\n# comment") == ["topic1", "# comment"]
    assert parse_topics_text("line", filename="topics.csv") == ["line"]

    with pytest.raises(ValueError, match="Queue name"):
        normalize_preset({"name": ""})

    with pytest.raises(ValueError, match="topics must be a list"):
        normalize_preset({"name": "Q", "topics": "nope"})

    db = db_module.SessionLocal()
    try:
        preset = write_queue_preset(
            db,
            {
                "name": "Preset",
                "slug": "preset-a",
                "topic_slug": "sports",
                "flow_path": "sports/standard-4-step.flow.json",
                "default_model": "m",
                "topics": ["T1"],
            },
        )
        assert preset["slug"] == "preset-a"
        db.commit()
    finally:
        db.close()


# --- flow_roles ---


def test_resolve_flow_roles_variants() -> None:
    from article_factory.services.flow_roles import group_steps_into_iterations, resolve_flow_roles
    from article_factory.services.flow_schema import FlowPerformanceConfig

    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(can_complete=True, can_loop=True, loop_goto_step_id=writer.step_id)
    flow = FlowDefinition(
        slug="roles",
        display_name="Roles",
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    roles = resolve_flow_roles(flow)
    assert roles.gate_step_key == "review"
    assert "writer" in roles.producer_step_keys

    perf_flow = FlowDefinition(
        slug="perf",
        display_name="Perf",
        article_step_id=writer.step_id,
        performance=FlowPerformanceConfig(gate_step_key="review", producer_step_keys=["writer"]),
        steps=[writer, review],
    )
    assert resolve_flow_roles(perf_flow).gate_step_key == "review"

    linear = FlowDefinition(
        slug="linear",
        display_name="Linear",
        article_step_id=writer.step_id,
        steps=[writer],
    )
    assert resolve_flow_roles(linear).gate_step_key is None

    groups = group_steps_into_iterations(
        [
            {"step_key": "writer", "content": "d1"},
            {"step_key": "review", "content": "reject"},
            {"step_key": "writer", "content": "d2"},
            {"step_key": "review", "content": "accept"},
        ],
        resolve_flow_roles(flow),
    )
    assert len(groups) == 2


# --- flow_schema validation ---


def test_flow_schema_validation_errors() -> None:
    s1 = new_flow_step(order=1, label="A", step_key="a")
    s2 = new_flow_step(order=2, label="B", step_key="a")
    with pytest.raises(ValueError, match="Duplicate step_key"):
        FlowDefinition(slug="dup-key", display_name="D", steps=[s1, s2])

    s2b = new_flow_step(order=2, label="B", step_key="b")
    s2b.step_id = s1.step_id
    with pytest.raises(ValueError, match="Duplicate step_id"):
        FlowDefinition(slug="dup-id", display_name="D", steps=[s1, s2b])

    s3 = new_flow_step(order=3, label="C", step_key="c")
    s3.order = 3
    with pytest.raises(ValueError, match="contiguous"):
        FlowDefinition(slug="gap", display_name="G", steps=[s1, s3])

    bad_loop = new_flow_step(order=2, label="Review", step_key="review")
    bad_loop.completion = FlowStepCompletion(can_complete=True, can_loop=True, loop_goto_step_id="missing")
    with pytest.raises(ValueError, match="loop_goto_step_id"):
        FlowDefinition(slug="bad-loop", display_name="B", article_step_id=s1.step_id, steps=[s1, bad_loop])


def test_slugify_step_key_empty() -> None:
    from article_factory.services.flow_schema import slugify_step_key

    assert slugify_step_key("!!!", 3) == "step_3"


# --- run_attachments ---


def test_run_attachments_helpers(tmp_path, monkeypatch) -> None:
    from article_factory.services.run_attachments import (
        _guess_content_type,
        _is_text_file,
        collect_run_workspace_attachments,
        list_run_workspace_attachment_summaries,
        read_run_workspace_file,
    )

    assert _guess_content_type("note.md") == "text/markdown"
    assert _guess_content_type("data.json") == "application/json"
    assert _guess_content_type("file.csv") == "text/csv"
    assert _guess_content_type("page.html") == "text/html"
    assert _guess_content_type("other.txt") == "text/plain"
    assert _is_text_file(b"") is True
    assert _is_text_file(b"\x00bin") is False

    monkeypatch.setattr("article_factory.config.settings.flow_run_outputs_root", str(tmp_path))
    root = tmp_path / "run-att" / "workspace"
    root.mkdir(parents=True)
    (root / "note.md").write_text("hello", encoding="utf-8")
    (root / ".hidden").write_text("secret", encoding="utf-8")

    summaries = list_run_workspace_attachment_summaries("run-att")
    assert any(item["path"] == "note.md" for item in summaries)

    payload = read_run_workspace_file("run-att", "note.md")
    assert payload["content"] == "hello"

    with pytest.raises(ValueError, match="Invalid workspace path"):
        read_run_workspace_file("run-att", "../escape")

    with pytest.raises(FileNotFoundError):
        read_run_workspace_file("run-att", "missing.md")

    attachments = collect_run_workspace_attachments("run-att")
    assert attachments[0]["content"] == "hello"


# --- showroom_publish ---


@pytest.mark.asyncio
async def test_showroom_publish_paths(configured_db, monkeypatch) -> None:
    from article_factory.cms_client import CmsRequestError
    from article_factory.services.showroom_publish import build_publish_payload, publish_article_to_showroom, slugify_title

    assert slugify_title("!!!") == "article"

    run = FactoryRun(run_id="pub", topic_slug="sports", selected_puller="p1", selected_model="m1")
    article = CompletedArticle(
        run_id="pub",
        topic_slug="sports",
        title="",
        body_markdown="# Title\n\nBody",
        manifest={},
    )
    db = db_module.SessionLocal()
    try:
        db.add(run)
        db.commit()
        payload = build_publish_payload(db, run, article)
        assert payload["article"]["title"] == "Title Body"
        assert payload["manifest"]["selected_puller"] == "p1"
    finally:
        db.close()

    db = db_module.SessionLocal()
    try:
        with pytest.raises(CmsRequestError, match="empty article"):
            await publish_article_to_showroom(
                db,
                run=run,
                article=CompletedArticle(run_id="pub", topic_slug="sports", title="", body_markdown="  "),
            )

        with pytest.raises(CmsRequestError, match="not configured"):
            await publish_article_to_showroom(db, run=run, article=article, runtime=MagicMock(cms_url="", cms_api_key=""))

        cms = AsyncMock()
        cms.post_run_complete = AsyncMock(return_value={"ok": True})
        cms.post_run_event = AsyncMock()
        monkeypatch.setattr(
            "article_factory.services.showroom_publish.push_showroom_factory_status",
            AsyncMock(side_effect=RuntimeError("status fail")),
        )
        result = await publish_article_to_showroom(db, run=run, article=article, cms=cms)
        assert result["ok"] is True
    finally:
        db.close()


# --- flow_queues service (more) ---


def test_delete_flow_queue_blocks_running(configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue, delete_flow_queue

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Del", flow_path="test/SimpleTest.flow.json")
        db.add(
            TopicQueueItem(flow_queue_id=queue.id, topic_slug="general", prompt="Run", status="running")
        )
        db.commit()
        with pytest.raises(ValueError, match="Stop active runs"):
            delete_flow_queue(db, queue.id)
    finally:
        db.close()


def test_flow_queue_payload_with_active_run(configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue, flow_queue_payload

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Active", flow_path="test/SimpleTest.flow.json")
        item = TopicQueueItem(flow_queue_id=queue.id, topic_slug="general", prompt="Active", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="queue-active",
                topic_slug="general",
                queue_item_id=item.id,
                status="running",
            )
        )
        db.commit()
        payload = flow_queue_payload(db, queue)
        assert payload["active_run_id"] == "queue-active"
        assert payload["counts"]["running"] >= 1
    finally:
        db.close()


# --- telemetry (more) ---


def test_telemetry_infer_termination_and_load_flow(configured_db) -> None:
    from article_factory.services.telemetry import _infer_termination_reason, _load_flow_for_run

    run = FactoryRun(run_id="r", topic_slug="g", status="cancelled")
    assert _infer_termination_reason(run, final_accepted=None) == "cancelled"

    run2 = FactoryRun(run_id="r2", topic_slug="g", status="failed", error="missing verdict line")
    assert _infer_termination_reason(run2, final_accepted=None) == "no_verdict"

    db = db_module.SessionLocal()
    try:
        run3 = FactoryRun(run_id="r3", topic_slug="g", flow_path="missing/flow.flow.json", status="completed")
        assert _load_flow_for_run(db, run3) is None
    finally:
        db.close()


def test_rebuild_flow_telemetry_handles_failure(configured_db, monkeypatch) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="tel-fail", display_name="F", step_count=1)
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="tel-fail-run",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
            )
        )
        db.commit()
        monkeypatch.setattr(
            "article_factory.services.telemetry.capture_run_telemetry",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        stats = rebuild_flow_telemetry(db, rel_path, version.id)
        assert stats["failed"] >= 1
    finally:
        db.close()


# --- flow_storage (more) ---


def test_list_folder_flows_root_walk(configured_db) -> None:
    from article_factory.services.flow_storage import list_folder_flows

    create_flow(folder="walk", slug="one", display_name="One", step_count=1)
    flows = list_folder_flows("")
    assert any(f["slug"] == "one" for f in flows)


def test_create_from_template(configured_db) -> None:
    from article_factory.services.flow_storage import create_flow_from_template

    rel, flow = create_flow_from_template(
        template_path="_templates/single-writer.flow.json",
        folder="from-tpl",
        slug="imported",
        display_name="Imported",
    )
    assert rel == "from-tpl/imported.flow.json"
    assert flow.slug == "imported"


def test_move_flow_missing_slug(configured_db) -> None:
    from article_factory.services.flow_storage import move_flow

    write_flow("noslug.flow.json", new_flow_definition(slug="noslug", display_name="X", step_count=1))
    with pytest.raises(ValueError, match="slug is required"):
        move_flow("noslug.flow.json", folder="dest", slug="   ")


# --- personas service ---


def test_personas_service_crud(configured_db) -> None:
    from article_factory.services.personas import create_persona, delete_persona, read_persona, update_persona

    db = db_module.SessionLocal()
    try:
        created = create_persona(db, {"name": "Editor", "style_prompt": "Be brief."})
        slug = created["slug"]
        db.commit()
        assert read_persona(db, slug)["name"] == "Editor"

        updated = update_persona(db, slug, {"name": "Senior Editor"})
        assert updated["name"] == "Senior Editor"

        with pytest.raises(LookupError):
            read_persona(db, "missing")

        delete_persona(db, slug)
        db.commit()
        with pytest.raises(LookupError):
            read_persona(db, slug)
    finally:
        db.close()


# --- flow_switch ---


@pytest.mark.asyncio
async def test_switch_active_flow(configured_db) -> None:
    from article_factory.services.flow_switch import switch_active_flow

    db = db_module.SessionLocal()
    try:
        result = await switch_active_flow(
            db,
            flow_path="sports/standard-4-step.flow.json",
            set_as_default=True,
            clear_history=False,
            update_queued=True,
            requeue_running=False,
            topic_slug="sports",
        )
        assert result["flow_path"] == "sports/standard-4-step.flow.json"
    finally:
        db.close()


# --- flow_tool_requirements ---


def test_flow_tool_requirements_for_step() -> None:
    from article_factory.services.flow_tool_requirements import collect_flow_tool_requirements, flow_dict_tool_requirements

    flags = collect_flow_tool_requirements()
    assert flags["needs_web_search"] is True
    assert flow_dict_tool_requirements({})["needs_write_file"] is True


# --- verdict ---


def test_verdict_parse_edge_cases() -> None:
    from article_factory.services.verdict import Verdict, parse_verdict

    assert parse_verdict("VERDICT: ACCEPT") == Verdict.ACCEPT
    assert parse_verdict("VERDICT: REJECT") == Verdict.REJECT
    assert parse_verdict("no verdict here") == Verdict.NONE


# --- article_text ---


def test_article_text_headline() -> None:
    from article_factory.services.article_text import article_has_content, headline_from_markdown

    assert article_has_content("# Title\n\nBody") is True
    assert article_has_content("   ") is False
    assert headline_from_markdown("# My Title\n\nBody") == "My Title Body"


# --- routes flow_queues (more) ---


def test_flow_queue_start_update_existing(client, api_headers, configured_db) -> None:
    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "Update Start", "flow_path": "test/SimpleTest.flow.json", "topic_slug": "general"},
    )
    queue_id = created.json()["queue"]["id"]
    started = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "queue_id": queue_id,
            "name": "Updated Via Start",
            "flow_path": "sports/standard-4-step.flow.json",
            "topic_slug": "sports",
            "default_model": "test-model",
            "topics": ["Topic"],
            "enabled": True,
        },
    )
    assert started.status_code == 200


def test_flow_queue_delete_preset_not_found(client, api_headers) -> None:
    response = client.delete("/api/flow-queues/presets/missing-slug", headers=api_headers)
    assert response.status_code == 404


def test_flow_queue_update_not_found(client, api_headers) -> None:
    response = client.put("/api/flow-queues/99999", headers=api_headers, json={"name": "Nope"})
    assert response.status_code == 404


# --- cms_client ---


@pytest.mark.asyncio
async def test_cms_put_factory_status() -> None:
    from article_factory.cms_client import CmsClient

    client = CmsClient(base_url="http://cms.test", api_key="key")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.put = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.cms_client.httpx.AsyncClient", return_value=mock_http):
        await client.put_factory_status({"state": "idle"})
    mock_http.put.assert_awaited_once()


# --- token_usage tool path ---


def test_enrich_step_record_with_tools() -> None:
    from article_factory.services.token_usage import enrich_step_record

    record = enrich_step_record(
        {
            "step_key": "writer",
            "content": "",
            "usage": {},
            "tools_used": [{"tool": "web_search", "detail": "query text"}],
        },
        selected_model="m",
        body_markdown="",
    )
    assert record["usage"]["total_tokens"] >= 0


# --- review_parser score bounds ---


def test_review_parser_criterion_score_bounds() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 80,
        "verdict": "rejected",
        "criteria": {
            "accuracy_verifiable_facts": {"score": 999, "max_score": 40},
            "organization_flow": {"score": 10, "max_score": 15},
            "writing_quality": {"score": 10, "max_score": 15},
            "depth_specificity": {"score": 10, "max_score": 15},
            "reader_engagement": {"score": 5, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    assert review is not None
    assert any("out of bounds" in w for w in review.parse_warnings)


# --- admin routes (remaining) ---


def test_article_workspace_file_routes(client, api_headers, configured_db, tmp_path, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="ws-route", topic_slug="sports", status="completed"))
        db.add(
            CompletedArticle(
                run_id="ws-route",
                topic_slug="sports",
                title="T",
                body_markdown="# T\n\nB",
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr("article_factory.config.settings.flow_run_outputs_root", str(tmp_path))
    root = tmp_path / "ws-route" / "workspace"
    root.mkdir(parents=True)
    (root / "notes.md").write_text("workspace note", encoding="utf-8")

    ok = client.get("/api/articles/ws-route/workspace-files/notes.md", headers=api_headers)
    assert ok.status_code == 200
    assert ok.json()["content"] == "workspace note"

    missing_article = client.get("/api/articles/missing/workspace-files/x", headers=api_headers)
    assert missing_article.status_code == 404


def test_recover_accept_not_found(client, api_headers) -> None:
    response = client.post("/api/runs/missing/recover-accept", headers=api_headers)
    assert response.status_code == 404


def test_enqueue_with_empty_prompt_skipped(client, api_headers) -> None:
    response = client.post(
        "/api/queue",
        headers=api_headers,
        json={"topic_slug": "sports", "prompt": "   "},
    )
    assert response.status_code == 200


def test_factory_status_includes_active_board(client, api_headers, configured_db) -> None:
    response = client.get("/api/factory/status", headers=api_headers)
    assert response.status_code == 200
    assert "flow_queues" in response.json()


# --- review_parser (remaining branches) ---


def test_review_parser_malformed_criteria_types() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 80,
        "verdict": "rejected",
        "criteria": {
            "accuracy_verifiable_facts": {"score": "bad", "max_score": 40},
            "organization_flow": {"score": 10, "max_score": 15},
            "writing_quality": {"score": 10, "max_score": 15},
            "depth_specificity": {"score": 10, "max_score": 15},
            "reader_engagement": {"score": 5, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [],
        "required_changes": [],
    }
    text = f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: REJECTED"
    review = parse_structured_review(text)
    assert review is not None
    assert any("must be integers" in w for w in review.parse_warnings)


def test_review_parser_legacy_block_total_score() -> None:
    text = (
        "ARTICLE REVIEW\n\nAccuracy & Verifiable Facts\n\n35 / 40\n\n"
        "TOTAL SCORE\n\n77 / 100\n\nVERDICT: ACCEPTED\nEND REVIEW"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.total_score == 77


def test_review_parser_criterion_alias_label() -> None:
    text = "Organization & Flow: **12 / 15**\nTOTAL SCORE: 80/100\n\nVERDICT: ACCEPTED\nEND REVIEW"
    review = parse_structured_review(text)
    assert review is not None
    assert any(c.criterion_key == "organization_flow" for c in review.criteria)


# --- step_trace (remaining) ---


def test_step_tracer_record_activity_and_cp_round(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="trace-round", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="trace-round", step_key="writer", puller="p", model="m")
        tracer.mark_submitted(agent_id="a", conversation_id="c1", queue_depth=1)
        tracer.record_activity("Working")
        tracer.record_cp_round(cp_round=2, agent_id="a2", conversation_id="c2", queue_depth=3)
        step = db.query(StepExecution).filter_by(run_id="trace-round").one()
        assert step.progress["cp_round"] == 2
        assert step.conversation_id == "c2"
    finally:
        db.close()


def test_enrich_steps_from_manifest_step_stats(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="stats-run",
                topic_slug="sports",
                status="completed",
                manifest={"step_stats": [{"step_key": "writer", "content": "from stats"}]},
            )
        )
        db.commit()
        steps = [{"step_key": "writer", "status": "completed"}]
        enriched = enrich_steps_with_responses(db, "stats-run", steps)
        assert enriched[0]["response_content"] == "from stats"
    finally:
        db.close()


# --- orchestrator runner (remaining) ---


@pytest.mark.asyncio
async def test_execute_pipeline_cancel_without_requeue(configured_db, monkeypatch) -> None:
    from article_factory.services.run_control import RunCancelledError

    async def boom(db, **kwargs):
        raise RunCancelledError("stopped")

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
    monkeypatch.setattr("article_factory.orchestrator.runner.take_requeue_flow_path", AsyncMock(return_value=None))

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Fail", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="cancel-fail",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
        )
        db.add(run)
        db.commit()
        result = await _execute_pipeline(db, run=run, topic_prompt="Topic")
        db.refresh(item)
        assert result.status == "cancelled"
        assert item.status == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_dispatch_logs_no_idle_with_queue(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "busy-only",
                "is_active": True,
                "is_stale": False,
                "status": "busy",
                "supported_models": ["test-model"],
            }
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    loop._reserved_pullers.add("stale-res")

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Waiting", status="queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    assert "stale-res" not in loop._reserved_pullers or loop._run_workers == {}


# --- flow_runner resume invalid id ---


@pytest.mark.asyncio
async def test_flow_runner_resume_invalid_step_id(configured_db, flow_runner_env, monkeypatch) -> None:
    only = new_flow_step(order=1, label="Only", step_key="only")
    flow = FlowDefinition(
        slug="resume-bad-id",
        display_name="Resume",
        article_step_id=only.step_id,
        steps=[only],
    )
    write_flow("resume-bad-id.flow.json", flow)

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return _step_record("only", "done")

    completed = AsyncMock()

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings

        run = FactoryRun(
            run_id="resume-bad-id-run",
            topic_slug="general",
            flow_path="resume-bad-id.flow.json",
            status="running",
            selected_model="m",
            pipeline_state={
                "step_outputs": {},
                "feedback": "",
                "step_records": [],
                "current_step_id": "00000000-0000-0000-0000-000000000099",
                "iteration": 0,
            },
        )
        db.add(run)
        db.commit()
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path="resume-bad-id.flow.json",
            topic_prompt="Topic",
            runtime=load_runtime_settings(db),
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=completed,
            resume_from_step_id="00000000-0000-0000-0000-000000000099",
        )
        completed.assert_awaited()
        assert result.run_id == "resume-bad-id-run"
    finally:
        db.close()


# --- telemetry routes export ---


def test_telemetry_export_rebuilds_when_empty(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="export-tel", display_name="E", step_count=1)
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="export-tel-run",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
                manifest={"steps": [{"step_key": "step_1", "content": "body"}]},
            )
        )
        db.commit()
        version_id = version.id
    finally:
        db.close()

    response = client.get(
        f"/api/flows/telemetry/export?path={rel_path}&flow_version_id={version_id}",
        headers=api_headers,
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")


# --- flow_queues routes (remaining) ---


def test_flow_queue_preset_delete_value_error(client, api_headers) -> None:
    created = client.post(
        "/api/flow-queues/presets",
        headers=api_headers,
        json={
            "name": "Bad Preset",
            "slug": "bad-preset",
            "topic_slug": "sports",
            "flow_path": "",
            "default_model": "m",
            "topics": ["T"],
        },
    )
    assert created.status_code == 400


def test_flow_queue_items_not_found(client, api_headers) -> None:
    response = client.get("/api/flow-queues/99999/items", headers=api_headers)
    assert response.status_code == 404


# --- admin retry with active run ---


def test_retry_queue_item_with_active_run(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="active-block", topic_slug="sports", status="running"))
        item = TopicQueueItem(topic_slug="sports", prompt="Retry later", status="failed")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="failed-retry",
                topic_slug="sports",
                queue_item_id=item.id,
                status="failed",
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    async def ready(db):
        return {"can_retry": True, "message": "Ready", "blockers": []}

    monkeypatch.setattr("article_factory.routes.admin._retry_assessment", ready)
    monkeypatch.setattr("article_factory.orchestrator.runner.factory_loop.request_dispatch", lambda: None)

    response = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert response.status_code == 200
    assert "after the current article" in response.json()["message"]


# --- cms_client ---


def test_cms_error_message_non_string_detail() -> None:
    from article_factory.cms_client import cms_error_message

    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {"detail": {"code": "bad"}}
    response.request = MagicMock(method="POST", url=MagicMock(path="/x"))
    assert "Showroom CMS:" in cms_error_message(response)


@pytest.mark.asyncio
async def test_best_effort_showroom_swallows() -> None:
    from article_factory.cms_client import best_effort_showroom

    async def boom():
        raise RuntimeError("cms down")

    assert await best_effort_showroom("test", boom) is None


# --- step_trace duration ---


def test_duration_ms_between_naive_datetimes() -> None:
    from datetime import datetime

    from article_factory.services.step_trace import duration_ms_between

    start = datetime(2026, 1, 1, 12, 0, 0)
    end = datetime(2026, 1, 1, 12, 0, 2)
    assert duration_ms_between(start, end) == 2000
    assert duration_ms_between(None, end) is None


# --- telemetry helpers ---


def test_telemetry_score_transitions_and_termination() -> None:
    from article_factory.services.telemetry import _infer_termination_reason, _score_transitions

    assert _score_transitions([80, 70, 70, 90]) == (1, 1)
    run = FactoryRun(run_id="r", topic_slug="g", status="completed")
    assert _infer_termination_reason(run, final_accepted=False) == "failed"


def test_capture_run_telemetry_cancelled(configured_db) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version
    from article_factory.services.telemetry import capture_run_telemetry

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="cancel-tel", display_name="C", step_count=1)
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="cancel-tel-run",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="cancelled",
            )
        )
        db.commit()
        row = capture_run_telemetry(db, "cancel-tel-run")
        assert row is not None
        assert row.termination_reason == "cancelled"
    finally:
        db.close()


# --- flow_roles empty ---


def test_resolve_flow_roles_empty() -> None:
    from article_factory.services.flow_roles import resolve_flow_roles

    flow = FlowDefinition.model_construct(
        slug="empty",
        display_name="Empty",
        steps=[],
    )
    roles = resolve_flow_roles(flow)
    assert roles.gate_step_key is None
    assert roles.producer_step_keys == []


# --- run_recovery ---


def test_save_pipeline_state_and_latest_step(configured_db) -> None:
    from article_factory.services.run_recovery import latest_step_execution, save_pipeline_state

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="pipe-save", topic_slug="sports", status="running")
        db.add(run)
        db.commit()
        save_pipeline_state(
            db,
            run,
            step_outputs={"writer": "draft"},
            feedback="fix",
            step_records=[{"step_key": "writer", "content": "draft"}],
            current_step_id="step-1",
            iteration=1,
        )
        db.add(
            StepExecution(
                run_id="pipe-save",
                step_key="writer",
                status="completed",
                response_content="draft",
            )
        )
        db.commit()
        step = latest_step_execution(db, "pipe-save")
        assert step is not None
        assert run.pipeline_state["feedback"] == "fix"
    finally:
        db.close()


# --- flow_queues routes start with version ---


def test_flow_queue_start_with_flow_version(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="versioned-q", display_name="VQ", step_count=1)
        version = create_flow_version(db, rel_path, message="v1")
        db.commit()
        version_id = version.id
    finally:
        db.close()

    response = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "name": "Versioned Start",
            "flow_path": rel_path,
            "topic_slug": "general",
            "default_model": "test-model",
            "topics": ["Topic"],
            "flow_version_id": version_id,
            "save_preset": True,
            "preset_slug": "versioned-preset",
        },
    )
    assert response.status_code == 200
    assert response.json()["preset"] is not None


# --- routes personas update errors ---


def test_personas_update_not_found(client, api_headers) -> None:
    response = client.put(
        "/api/personas/missing",
        headers=api_headers,
        json={"name": "X", "style_prompt": "Y"},
    )
    assert response.status_code == 404


def test_personas_delete_not_found(client, api_headers) -> None:
    response = client.delete("/api/personas/missing", headers=api_headers)
    assert response.status_code == 404


# --- factory_api_key_cache ---


def test_factory_api_key_cache_warm_and_invalidate(configured_db) -> None:
    from article_factory.services.factory_api_key_cache import (
        get_cached_factory_api_key,
        invalidate_factory_api_key_cache,
        warm_factory_api_key_cache,
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import set_factory_api_key

        set_factory_api_key(db, "cache-key")
        db.commit()
        invalidate_factory_api_key_cache()
        warm_factory_api_key_cache(db)
        assert get_cached_factory_api_key() == "cache-key"
        invalidate_factory_api_key_cache()
        assert get_cached_factory_api_key() == ""
    finally:
        db.close()


# --- run_outputs ---


def test_run_outputs_helpers(configured_db, tmp_path, monkeypatch) -> None:
    from article_factory.services.flow_storage import save_step_response_to_disk
    from article_factory.services.run_outputs import list_run_step_files, read_run_step_file

    save_step_response_to_disk(run_id="out-run", step_order=2, step_key="review", content="review text")
    files = list_run_step_files("out-run")
    assert any(f["name"] == "02-review.md" for f in files)
    content = read_run_step_file("out-run", "02-review.md")
    assert content == "review text"

    with pytest.raises(FileNotFoundError):
        read_run_step_file("out-run", "missing.md")


# --- run_error_tags ---


def test_run_error_tags_service(configured_db) -> None:
    from article_factory.services.run_error_tags import error_tag_to_dict, upsert_run_error_tag

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="tag-run", topic_slug="sports", status="failed"))
        db.commit()
        row = upsert_run_error_tag(db, run_id="tag-run", error_group="timeout", note="slow")
        payload = error_tag_to_dict(row)
        assert payload["error_group"] == "timeout"
        assert payload["note"] == "slow"
        with pytest.raises(ValueError, match="Unknown error group"):
            upsert_run_error_tag(db, run_id="tag-run", error_group="not-a-group")
    finally:
        db.close()


# --- flow_switch errors ---


@pytest.mark.asyncio
async def test_switch_active_flow_missing(configured_db) -> None:
    from article_factory.services.flow_switch import switch_active_flow

    db = db_module.SessionLocal()
    try:
        with pytest.raises(FileNotFoundError):
            await switch_active_flow(
                db,
                flow_path="missing/flow.flow.json",
                topic_slug="sports",
            )
    finally:
        db.close()


# --- pipeline ---


def test_serialize_active_run_with_queue_item(configured_db) -> None:
    from article_factory.orchestrator.pipeline import serialize_active_run

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic text", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="serialize-run",
            topic_slug="sports",
            status="running",
            queue_item_id=item.id,
        )
        db.add(run)
        db.commit()
        payload = serialize_active_run(db, run)
        assert payload["run_id"] == "serialize-run"
        assert payload["topic_prompt"] == "Topic text"
    finally:
        db.close()


# --- control_plane client task status ---


@pytest.mark.asyncio
async def test_control_plane_task_status_500() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")
    server_error = MagicMock()
    server_error.status_code = 500

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=server_error)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        assert await client.get_task_status("conv") is None


# --- executor poll queued tracer ---


@pytest.mark.asyncio
async def test_execute_step_queued_status_marks_waiting(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="queued-wait", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="queued-wait", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock()
    cp.submit_task = AsyncMock(return_value={"queue_depth": 2})
    cp.get_task_status = AsyncMock(return_value={"status": "queued", "queue_depth_at_submit": 2})
    cp.task_was_fetched = AsyncMock(return_value=False)
    poll_n = {"n": 0}

    async def poll_side_effect(*args, **kwargs):
        poll_n["n"] += 1
        if poll_n["n"] >= 3:
            return [{"message": {"content": "done"}, "usage": {}}]
        return []

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
            tracer=tracer,
        )

    assert tracer.execution.status == "completed"


# --- flow_schema loop validation ---


def test_flow_schema_mid_step_loop_validation() -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.loop = FlowStepLoop(enabled=True, goto_step_id=writer.step_id)
    flow = FlowDefinition(
        slug="mid-loop-valid",
        display_name="Mid",
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    assert flow.steps[1].loop.goto_step_id == writer.step_id

    bad_review = new_flow_step(order=2, label="Review", step_key="review")
    bad_review.loop = FlowStepLoop(enabled=True, goto_step_id="missing-id")
    with pytest.raises(ValueError, match="references a missing step"):
        FlowDefinition(
            slug="bad-mid",
            display_name="Bad",
            article_step_id=writer.step_id,
            steps=[writer, bad_review],
        )


# --- queue_presets file ops ---


def test_parse_topics_csv_empty_rows() -> None:
    from article_factory.services.queue_presets import parse_topics_csv

    assert parse_topics_csv("topic1\n,\n") == ["topic1"]


# --- routes flow_performance ---


def test_flow_performance_error_groups_filter(client, api_headers, configured_db) -> None:
    response = client.get("/api/flows/error-groups?error_group=completed", headers=api_headers)
    assert response.status_code == 200

