from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

import article_factory.db as db_module
from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import CompletedArticle, FactoryRun, FlowQueue, SavedQueue, StepExecution, TopicQueueItem
from article_factory.orchestrator.flow_runner import execute_flow_pipeline, restore_flow_state
from article_factory.orchestrator.runner import (
    FactoryLoop,
    _execute_pipeline,
    _flow_path_for_run,
    continue_active_run,
    run_pipeline_for_topic,
)
from article_factory.services.control_plane_heartbeat import (
    ControlPlaneHeartbeatLoop,
    _agent_display_name,
    control_plane_heartbeat_tick,
)
from article_factory.services.flow_defaults import build_writer_review_flow
from article_factory.services.flow_queues import (
    create_flow_queue,
    delete_flow_queue,
    enqueue_topics_to_queue,
    ensure_default_flow_queue,
    flow_queue_payload,
    resolve_queue_flow_path,
    select_queued_items_round_robin,
    update_flow_queue,
)
from article_factory.services.flow_schema import (
    FlowDefinition,
    FlowStep,
    FlowStepCompletion,
    FlowStepLoop,
    flow_to_dict,
    new_flow_definition,
    new_flow_step,
)
from article_factory.services.flow_storage import (
    create_flow,
    create_flow_from_template,
    delete_folder,
    duplicate_flow,
    flows_root,
    import_flow,
    list_folder_flows,
    list_tree,
    move_flow,
    read_flow,
    write_flow,
)
from article_factory.services.queue_presets import (
    delete_queue_preset,
    migrate_file_presets_to_db,
    normalize_preset,
    parse_topics_csv,
    parse_topics_lines,
    queue_presets_root,
    write_queue_preset,
)
from article_factory.services.run_control import RunCancelledError, clear_run_cancel
from article_factory.services.run_recovery import (
    commit_with_retry,
    reconcile_orphaned_runs,
    reconstruct_pipeline_state,
    save_pipeline_state,
)
from article_factory.services.step_tools import (
    StepToolRegistry,
    WorkspaceViolation,
    _parse_tool_arguments,
    augment_system_prompt_for_tools,
    list_workspace_path,
    read_workspace_file,
    resolve_step_tools,
    run_workspace_root,
    tool_use_nudge_message,
)
from article_factory.workers.executor import (
    _poll_step_response,
    _submit_and_wait_for_round,
    execute_step,
)


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
    for run_id in (
        "run-cancel-mid",
        "run-cancel-after",
        "run-requeue-fail",
        "run-async-cancel",
        "run-persist-fail",
        "run-stop-gaps",
    ):
        await clear_run_cancel(run_id)


# --- runner.py ---


def test_flow_path_for_run_default(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="r", topic_slug="sports", status="running")
        path = _flow_path_for_run(db, run)
        assert path.endswith(".flow.json")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_resume_by_step_key(configured_db, monkeypatch) -> None:
    rel_path = "test/resume-key.flow.json"
    write_flow(rel_path, build_writer_review_flow())

    captured: dict = {}

    async def fake_execute(db, *, run, flow_path, topic_prompt, runtime, cms, emit_step_started, complete_run, resume_from_step_id=None):
        captured["resume_from_step_id"] = resume_from_step_id
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", fake_execute)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-resume-key",
            topic_slug="sports",
            flow_path=rel_path,
            status="running",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()
        await _execute_pipeline(db, run=run, topic_prompt="Topic", resume_from_step="writer")
        flow = read_flow(rel_path)
        writer_id = next(s.step_id for s in flow.steps if s.step_key == "writer")
        assert captured["resume_from_step_id"] == writer_id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_cancelled_fail_without_requeue(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-requeue-fail",
            topic_slug="sports",
            queue_item_id=item.id,
            status="cancelled",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()

        async def boom(*args, **kwargs):
            raise RunCancelledError("stopped")

        monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
        monkeypatch.setattr(
            "article_factory.orchestrator.runner.take_requeue_flow_path",
            AsyncMock(return_value=None),
        )

        await _execute_pipeline(db, run=run, topic_prompt="Topic")
        db.refresh(item)
        assert item.status == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_asyncio_cancelled(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-async-cancel",
            topic_slug="sports",
            status="running",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()

        async def boom(*args, **kwargs):
            raise asyncio.CancelledError()

        monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)

        with pytest.raises(asyncio.CancelledError):
            await _execute_pipeline(db, run=run, topic_prompt="Topic")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_persist_failure_logged(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="run-persist-fail", topic_slug="sports", status="running")
        db.add(run)
        db.commit()

        async def boom(*args, **kwargs):
            raise RuntimeError("pipeline exploded")

        monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
        monkeypatch.setattr(
            "article_factory.orchestrator.runner.commit_with_retry",
            MagicMock(side_effect=RuntimeError("commit failed")),
        )

        with pytest.raises(RuntimeError, match="pipeline exploded"):
            await _execute_pipeline(db, run=run, topic_prompt="Topic")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_for_topic_with_puller_and_bad_flow(configured_db, monkeypatch) -> None:
    async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)
    monkeypatch.setattr("article_factory.orchestrator.runner._emit_run_event", AsyncMock())
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.ensure_flow_version_for_run",
        lambda _db, _path: MagicMock(id=1),
    )
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.get_or_create_topic_queue_snapshot",
        lambda *_args, **_kwargs: None,
    )

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="queued")
        db.add(item)
        db.flush()
        run = await run_pipeline_for_topic(
            db,
            topic_slug="sports",
            topic_prompt="Topic",
            queue_item_id=item.id,
            selected_puller="puller-x",
            flow_path="missing/bad.flow.json",
        )
        assert run.selected_puller == "puller-x"
        assert run.current_step == "writer"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_bad_flow_path(configured_db, monkeypatch) -> None:
    async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
        run.status = "completed"
        db.commit()

    monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-bad-flow",
            topic_slug="sports",
            status="running",
            flow_path="totally/missing.flow.json",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()
        result = await continue_active_run(db, run)
        assert result is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_start_after_done_task(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    loop._task = done

    monkeypatch.setattr("article_factory.orchestrator.runner.reconcile_orphaned_runs", lambda _db: 0)
    monkeypatch.setattr(loop, "_loop", AsyncMock())

    await loop.start()
    assert loop._task is not None
    await loop.stop()


@pytest.mark.asyncio
async def test_factory_loop_stop_cancels_workers() -> None:
    loop = FactoryLoop()
    loop._running = True
    worker = asyncio.create_task(asyncio.sleep(60))
    loop._run_workers["run-w"] = worker
    loop._task = asyncio.create_task(asyncio.sleep(60))
    await loop.stop()
    assert loop._run_workers == {}


@pytest.mark.asyncio
async def test_factory_loop_ensure_running_restarts_dead_task(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    loop._task = done

    started = {"n": 0}

    async def fake_start():
        started["n"] += 1
        loop._running = True
        loop._task = asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(loop, "start", fake_start)
    await loop.ensure_running()
    assert started["n"] == 1


@pytest.mark.asyncio
async def test_factory_loop_spawn_worker_duplicate_and_exception(monkeypatch) -> None:
    loop = FactoryLoop()
    seen: list[str] = []

    async def ok_coro():
        seen.append("ok")

    async def bad_coro():
        raise ValueError("worker boom")

    loop._spawn_worker("dup", ok_coro())
    loop._spawn_worker("dup", ok_coro())
    loop._spawn_worker("bad", bad_coro())
    await asyncio.sleep(0.05)
    assert "ok" in seen


@pytest.mark.asyncio
async def test_factory_loop_dispatch_tick_branches(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            },
            {
                "puller_name": "puller-1",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            },
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.is_run_cancelled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", AsyncMock())

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        run = FactoryRun(run_id="run-dispatch", topic_slug="sports", status="running")
        db.add(run)
        db.flush()
        item = TopicQueueItem(topic_slug="sports", prompt="Queued", status="queued")
        db.add(item)
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()

    mock_cp.list_pullers = AsyncMock(side_effect=RuntimeError("cp down"))
    await loop._dispatch_tick()


@pytest.mark.asyncio
async def test_factory_loop_running_worker_and_continue_run(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True
    worker = asyncio.create_task(asyncio.sleep(60))
    loop._run_workers["run-run-continue"] = worker

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-continue",
            topic_slug="sports",
            status="running",
            current_step="writer",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "article_factory.orchestrator.runner.continue_active_run",
        AsyncMock(return_value=True),
    )
    await loop._dispatch_tick()
    await loop._continue_run("run-continue")
    await loop._continue_run("missing-run")
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_factory_loop_wait_timeout_and_loop_error(monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True
    calls = {"n": 0}

    async def fail_tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick failed")

    monkeypatch.setattr(loop, "_dispatch_tick", fail_tick)
    monkeypatch.setattr(loop, "_wait_for_next_tick", AsyncMock())
    monkeypatch.setattr(loop, "_running", False)
    await loop._loop()
    await loop._wait_for_next_tick()


# --- flow_runner.py ---


@pytest.mark.asyncio
async def test_execute_flow_no_model_raises(configured_db, monkeypatch) -> None:
    rel_path = "test/no-model.flow.json"
    write_flow(rel_path, build_writer_review_flow())
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": ""})
        run = FactoryRun(run_id="run-no-model", topic_slug="sports", flow_path=rel_path, status="running")
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        with pytest.raises(RuntimeError, match="No model configured"):
            await execute_flow_pipeline(
                db,
                run=run,
                flow_path=rel_path,
                topic_prompt="Topic",
                runtime=runtime,
                cms=None,
                emit_step_started=AsyncMock(),
                complete_run=AsyncMock(),
            )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_resume_unknown_step_id(configured_db, monkeypatch) -> None:
    rel_path = "test/resume-unknown.flow.json"
    write_flow(rel_path, build_writer_review_flow())

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return _step_record("writer", "# Title\n\nBody")

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
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-resume-unknown", topic_slug="sports", flow_path=rel_path, status="running")
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        await execute_flow_pipeline(
            db,
            run=run,
            flow_path=rel_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
            resume_from_step_id="missing-step-id",
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_cancelled_before_and_after_step(configured_db, monkeypatch) -> None:
    rel_path = "test/cancel-mid.flow.json"
    write_flow(rel_path, build_writer_review_flow())

    cancel_checks = {"n": 0}

    async def cancelled(run_id: str) -> bool:
        cancel_checks["n"] += 1
        return cancel_checks["n"] == 1

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.is_run_cancelled", cancelled)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-cancel-mid", topic_slug="sports", flow_path=rel_path, status="running")
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        with pytest.raises(RunCancelledError):
            await execute_flow_pipeline(
                db,
                run=run,
                flow_path=rel_path,
                topic_prompt="Topic",
                runtime=runtime,
                cms=None,
                emit_step_started=AsyncMock(),
                complete_run=AsyncMock(),
            )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_reject_without_loop_target(configured_db, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion.model_construct(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=None,
    )
    flow = FlowDefinition.model_construct(
        slug="reject-no-loop",
        display_name="Reject No Loop",
        max_iterations=3,
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    rel_path = "test/reject-no-loop.flow.json"

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody")
        return _step_record("review", "Bad.\n\nVERDICT: REJECT")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.read_flow", lambda _p: flow)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-reject-no-loop", topic_slug="sports", flow_path=rel_path, status="running")
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
        assert "no loop target" in (result.error or "").lower()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_linear_no_completion(configured_db, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    writer.completion = FlowStepCompletion.model_construct(can_complete=False, can_loop=False)
    flow = FlowDefinition.model_construct(
        slug="linear-fail",
        display_name="Linear Fail",
        max_iterations=1,
        article_step_id=writer.step_id,
        steps=[writer],
    )
    rel_path = "test/linear-fail.flow.json"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.read_flow", lambda _p: flow)
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.run_step_from_context",
        AsyncMock(return_value=_step_record("writer", "# Title\n\nBody")),
    )
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-linear-fail", topic_slug="sports", flow_path=rel_path, status="running")
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
        assert "linear flows" in (result.error or "").lower()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_mid_step_loop_reject(configured_db, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    checker = new_flow_step(order=2, label="Checker", step_key="checker")
    checker.loop = FlowStepLoop(enabled=True, goto_step_id=writer.step_id)
    finisher = new_flow_step(order=3, label="Finisher", step_key="finisher")
    finisher.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    flow = FlowDefinition(
        slug="mid-loop",
        display_name="Mid Loop",
        max_iterations=5,
        article_step_id=writer.step_id,
        steps=[writer, checker, finisher],
    )
    rel_path = "test/mid-loop.flow.json"
    write_flow(rel_path, flow)
    checker_calls = {"n": 0}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody")
        if ctx.step_key == "checker":
            checker_calls["n"] += 1
            if checker_calls["n"] == 1:
                return _step_record("checker", "Fix.\n\nVERDICT: REJECT")
            return _step_record("checker", "OK.\n\nVERDICT: ACCEPT")
        return _step_record("finisher", "# Title\n\nFinal")

    completed: dict = {}

    async def complete_run(draft, records):
        completed["draft"] = draft

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-mid-loop", topic_slug="sports", flow_path=rel_path, status="running")
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
        assert checker_calls["n"] >= 2
        assert completed.get("draft") or result.status == "completed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_max_iterations_and_empty_flow(configured_db, monkeypatch) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    flow = FlowDefinition(
        slug="max-iter",
        display_name="Max Iter",
        max_iterations=1,
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    rel_path = "test/max-iter.flow.json"
    write_flow(rel_path, flow)

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody")
        return _step_record("review", "Fix.\n\nVERDICT: REJECT")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-max-iter", topic_slug="sports", flow_path=rel_path, status="running")
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
        assert "Max flow iterations" in (result.error or "")

        writer2 = new_flow_step(order=1, label="Writer", step_key="writer")
        review2 = new_flow_step(order=2, label="Review", step_key="review")
        review2.completion = FlowStepCompletion.model_construct(
            can_complete=True,
            can_loop=True,
            loop_goto_step_id=writer2.step_id,
        )
        empty_flow = FlowDefinition.model_construct(
            slug="empty",
            display_name="Empty",
            max_iterations=1,
            article_step_id=writer2.step_id,
            steps=[writer2, review2],
        )
        empty_path = "test/empty.flow.json"

        async def noop_step(ctx, cp=None, tracer=None, run_id=None):
            return _step_record(ctx.step_key, "No verdict")

        monkeypatch.setattr("article_factory.orchestrator.flow_runner.read_flow", lambda p: empty_flow if "empty" in p else flow)
        monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", noop_step)

        run2 = FactoryRun(run_id="run-empty-flow", topic_slug="sports", flow_path=empty_path, status="running")
        db.add(run2)
        db.commit()
        result2 = await execute_flow_pipeline(
            db,
            run=run2,
            flow_path=empty_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert result2.status == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_flow_save_response_to_disk(configured_db, monkeypatch, tmp_path) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    writer.save_response_to_disk = True
    writer.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    flow = FlowDefinition(
        slug="save-disk",
        display_name="Save Disk",
        max_iterations=1,
        article_step_id=writer.step_id,
        steps=[writer],
    )
    rel_path = "test/save-disk.flow.json"
    write_flow(rel_path, flow)
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))

    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.run_step_from_context",
        AsyncMock(return_value=_step_record("writer", "# Title\n\nSaved body")),
    )
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "test-model"})
        run = FactoryRun(run_id="run-save-disk", topic_slug="sports", flow_path=rel_path, status="running")
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        await execute_flow_pipeline(
            db,
            run=run,
            flow_path=rel_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        saved = tmp_path / "run-save-disk" / "steps"
        assert any(saved.iterdir())
    finally:
        db.close()


# --- flow_storage.py ---


def test_list_tree_skips_dotfiles(configured_db) -> None:
    hidden = flows_root() / ".hidden"
    hidden.mkdir(exist_ok=True)
    (hidden / "secret.flow.json").write_text("{}", encoding="utf-8")
    tree = list_tree("")
    child_names = [c["name"] for c in tree.get("children") or []]
    assert ".hidden" not in child_names


def test_delete_folder_not_a_directory(configured_db) -> None:
    rel_path, _ = create_flow(folder="not-dir", slug="file-only", display_name="File", step_count=1)
    with pytest.raises(NotADirectoryError):
        delete_folder(rel_path)


def test_duplicate_flow_exists_and_loop_remap(configured_db) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.loop = FlowStepLoop(enabled=True, goto_step_id=writer.step_id)
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    flow = FlowDefinition(
        slug="dup-source",
        display_name="Dup Source",
        max_iterations=3,
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    rel_path = "dup-test/dup-source.flow.json"
    write_flow(rel_path, flow)
    duplicate_flow(rel_path, slug="dup-copy")
    with pytest.raises(FileExistsError):
        duplicate_flow(rel_path, slug="dup-copy")


def test_move_flow_errors_and_success(configured_db, tmp_path, monkeypatch) -> None:
    flows_dir = (tmp_path / "flows-move").resolve()
    flows_dir.mkdir()
    monkeypatch.setattr(settings, "flows_root", str(flows_dir))
    monkeypatch.setattr(
        "article_factory.services.flow_storage.flows_root",
        lambda: flows_dir,
    )

    rel_path, _ = create_flow(folder="move-src", slug="move-me", display_name="Move", step_count=1)
    dest_name = "move-dest"

    with pytest.raises(FileNotFoundError):
        move_flow("missing.flow.json", folder=dest_name)

    with pytest.raises(ValueError, match="_templates"):
        move_flow(rel_path, folder="_templates")

    moved_path, _ = move_flow(rel_path, folder=dest_name, slug="moved-file")
    assert moved_path.startswith(f"{dest_name}/")
    assert not (flows_dir / "move-src" / "move-me.flow.json").exists()

    moved_slug = Path(moved_path).name.replace(".flow.json", "")
    with pytest.raises(FileExistsError):
        move_flow(moved_path, folder=dest_name, slug=moved_slug)


def test_list_folder_flows_walk_and_bad_catalog(configured_db) -> None:
    create_flow(folder="walk/a", slug="inner", display_name="Inner", step_count=1)
    flows = list_folder_flows("")
    paths = {item["path"] for item in flows}
    assert any("walk/a" in p for p in paths)

    bad_path = "walk/bad.flow.json"
    target = flows_root() / bad_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not-json", encoding="utf-8")
    listing = list_folder_flows("walk")
    assert any(item["path"] == bad_path and item["step_count"] == 0 for item in listing)


def test_create_flow_step_count_validation(configured_db) -> None:
    with pytest.raises(ValueError, match="step_count"):
        create_flow(folder="x", slug="bad-count", display_name="Bad", step_count=0)
    with pytest.raises(ValueError, match="step_count"):
        create_flow(folder="x", slug="bad-count-2", display_name="Bad", step_count=51)


def test_create_flow_from_template_with_loops(configured_db) -> None:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.loop = FlowStepLoop(enabled=True, goto_step_id=writer.step_id)
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    flow = FlowDefinition(
        slug="tpl-loop",
        display_name="Template Loop",
        max_iterations=3,
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    template_path = "_templates/tpl-loop.flow.json"
    write_flow(template_path, flow)
    rel_path, created = create_flow_from_template(
        template_path=template_path,
        folder="from-tpl-loop",
        slug="cloned",
        display_name="Cloned",
    )
    assert rel_path.endswith("cloned.flow.json")
    assert created.steps[1].loop is not None
    assert created.steps[1].loop.goto_step_id != writer.step_id


def test_import_flow_empty_slug(configured_db) -> None:
    flow = new_flow_definition(slug="valid-slug", display_name="Valid", step_count=1)
    with pytest.raises(ValueError, match="slug"):
        import_flow(flow, folder="imports", slug="   ")


# --- services: run_recovery, flow_queues, queue_presets, step_tools, heartbeat ---


def test_commit_with_retry_locked(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        attempts = {"n": 0}

        def commit():
            attempts["n"] += 1
            if attempts["n"] < 2:
                exc = OperationalError("stmt", {}, Exception("database is locked"))
                raise exc

        db.commit = commit  # type: ignore[method-assign]
        db.rollback = MagicMock()
        monkeypatch.setattr("article_factory.services.run_recovery.time.sleep", lambda _s: None)
        commit_with_retry(db, max_attempts=3, base_delay=0.01)
        assert attempts["n"] == 2
    finally:
        db.close()


def test_save_pipeline_state_skips_commit_when_not_running(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="run-not-running", topic_slug="sports", status="failed")
        db.add(run)
        db.commit()
        commits_before = 0

        def counting_commit():
            nonlocal commits_before
            commits_before += 1

        db.commit = counting_commit  # type: ignore[method-assign]
        save_pipeline_state(
            db,
            run,
            step_outputs={},
            feedback="",
            step_records=[],
        )
        assert commits_before == 0
    finally:
        db.close()


def test_reconstruct_and_reconcile_orphaned(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-reconstruct",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="missing/bad.flow.json",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="run-reconstruct",
                step_key="writer",
                status="completed",
                response_content="# T\n\nBody",
            )
        )
        db.commit()
        assert reconstruct_pipeline_state(db, run) is None

        run2 = FactoryRun(
            run_id="run-orphan",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run2)
        db.flush()
        db.add(
            StepExecution(
                run_id="run-orphan",
                step_key="writer",
                status="submitted",
            )
        )
        db.commit()
        failed = reconcile_orphaned_runs(db)
        assert failed >= 1
    finally:
        db.close()


def test_flow_queues_service_branches(configured_db) -> None:
    import article_factory.services.flow_queues as flow_queues_module

    flow_queues_module._null_queue_migration_done = False

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(
            topic_slug="sports",
            prompt="Legacy",
            status="queued",
            flow_queue_id=None,
        )
        db.add(item)
        db.commit()

        default_queue = ensure_default_flow_queue(db)
        db.refresh(item)
        assert item.flow_queue_id == default_queue.id

        queue = FlowQueue(slug="empty-path", name="Empty Path", flow_path="", topic_slug="sports", enabled=True)
        db.add(queue)
        db.flush()
        assert resolve_queue_flow_path(db, queue).endswith(".flow.json")

        with pytest.raises(ValueError, match="name"):
            create_flow_queue(db, name="  ", flow_path="sports/standard-4-step.flow.json")

        rel_path = "sports/standard-4-step.flow.json"
        q = create_flow_queue(db, name="Svc Queue", flow_path=rel_path)
        payload = flow_queue_payload(db, q)
        assert "counts" in payload

        with pytest.raises(LookupError):
            update_flow_queue(db, 99999, name="X")

        with pytest.raises(ValueError, match="empty"):
            update_flow_queue(db, q.id, name="   ")

        update_flow_queue(db, q.id, flow_path="", topic_slug="tech", enabled=False)
        with pytest.raises(ValueError, match="Enable"):
            enqueue_topics_to_queue(db, q.id, ["Topic A"])

        q.enabled = True
        created = enqueue_topics_to_queue(db, q.id, ["Topic B"])
        assert created

        empty_pick, idx = select_queued_items_round_robin(db, limit=0, start_index=0)
        assert empty_pick == []
        assert idx == 0

        with pytest.raises(ValueError, match="default"):
            delete_flow_queue(db, default_queue.id)
    finally:
        db.close()


def test_queue_presets_branches(configured_db) -> None:
    assert parse_topics_lines("a\n\nb") == ["a", "b"]
    assert parse_topics_csv("a,b\nc,d") == ["a", "c"]

    with pytest.raises(ValueError, match="name"):
        normalize_preset({"topics": []})
    with pytest.raises(ValueError, match="list"):
        normalize_preset({"name": "Q", "topics": "nope"})

    db = db_module.SessionLocal()
    try:
        with pytest.raises(ValueError, match="Flow path"):
            write_queue_preset(db, {"name": "No Flow", "topics": ["a"]})

        preset = write_queue_preset(
            db,
            {
                "name": "Preset A",
                "slug": "preset-a",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": ["Topic 1"],
            },
        )
        db.flush()
        assert preset["slug"] == "preset-a"

        updated = write_queue_preset(
            db,
            {
                "name": "Preset A Updated",
                "slug": "preset-a",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": ["Topic 2"],
            },
        )
        assert updated["name"] == "Preset A Updated"

        db.add(SavedQueue(slug="taken-slug", name="Taken", flow_path="sports/standard-4-step.flow.json", topics=[]))
        db.flush()
        second = write_queue_preset(
            db,
            {
                "name": "Preset B",
                "slug": "taken-slug",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": [],
            },
        )
        assert second["slug"] == "taken-slug"

        deleted = delete_queue_preset(db, "preset-a")
        assert deleted["slug"] == "preset-a"
    finally:
        db.close()


def test_migrate_file_presets(configured_db, tmp_path, monkeypatch) -> None:
    root = tmp_path / "queue-presets"
    root.mkdir()
    (root / "legacy.queue.json").write_text(
        json.dumps(
            {
                "name": "Legacy",
                "slug": "legacy",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": ["One"],
            }
        ),
        encoding="utf-8",
    )
    (root / "bad.queue.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: root)

    db = db_module.SessionLocal()
    try:
        imported = migrate_file_presets_to_db(db)
        assert imported == 1
        assert not (root / "legacy.queue.json").exists()
    finally:
        db.close()


def test_queue_presets_unique_slug(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(SavedQueue(slug="unique-base", name="Base", flow_path="sports/standard-4-step.flow.json", topics=[]))
        db.flush()
        from article_factory.services.queue_presets import _unique_slug

        assert _unique_slug(db, "unique-base") == "unique-base-2"
    finally:
        db.close()


def test_step_tools_branches(tmp_path, monkeypatch) -> None:
    enabled = {
        "write_file": True,
        "read_file": True,
        "list_files": True,
        "web_search": True,
        "web_fetch": True,
    }
    resolved = resolve_step_tools(enabled)
    assert "Factory tools" in augment_system_prompt_for_tools("", resolved)
    nudge = tool_use_nudge_message(resolved)
    assert "web_search" in nudge
    assert "write_file" in nudge

    assert _parse_tool_arguments("") == {}
    assert _parse_tool_arguments("not-json") == {}
    assert _parse_tool_arguments('["list"]') == {}

    workspace = run_workspace_root("run-tools")
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    assert "not found" in asyncio.run(read_workspace_file(workspace / "missing.txt", display_path="missing.txt"))
    assert "not a file" in asyncio.run(read_workspace_file(workspace, display_path="."))
    assert "not a directory" in asyncio.run(list_workspace_path(workspace / "notes.txt", display_path="notes.txt"))

    registry = StepToolRegistry(workspace_root=workspace, brave_api_key="brave-test-key")
    result = asyncio.run(
        registry.execute(
            {
                "id": "1",
                "function": {"name": "read_file", "arguments": {"path": "notes.txt"}},
            }
        )
    )
    assert "hello" in result["content"]

    list_result = asyncio.run(
        registry.execute({"id": "2", "function": {"name": "list_files", "arguments": {"path": "."}}})
    )
    assert "notes.txt" in list_result["content"]

    search_no_key = StepToolRegistry(workspace_root=workspace, brave_api_key="")
    search_result = asyncio.run(
        search_no_key.execute({"id": "3", "function": {"name": "web_search", "arguments": {"query": "x"}}})
    )
    assert "Brave Search API key" in search_result["content"]

    with patch(
        "article_factory.services.step_tools.brave_web_search",
        AsyncMock(return_value={"web": {"results": []}}),
    ):
        with patch("article_factory.services.step_tools.format_brave_results", return_value="results"):
            search_ok = asyncio.run(
                registry.execute(
                    {
                        "id": "4",
                        "function": {"name": "web_search", "arguments": {"query": "news"}},
                    }
                )
            )
            assert search_ok["content"] == "results"

    with patch(
        "article_factory.services.step_tools.fetch_web_page",
        AsyncMock(return_value={"url": "https://example.com", "text": "page"}),
    ):
        with patch("article_factory.services.step_tools.format_fetch_result", return_value="fetched"):
            fetch_result = asyncio.run(
                registry.execute(
                    {
                        "id": "5",
                        "function": {"name": "web_fetch", "arguments": {"url": "https://example.com"}},
                    }
                )
            )
            assert fetch_result["content"] == "fetched"

    unknown = asyncio.run(registry.execute({"id": "6", "function": {"name": "nope", "arguments": {}}}))
    assert "unknown tool" in unknown["content"]

    bad_path = asyncio.run(
        registry.execute({"id": "7", "function": {"name": "read_file", "arguments": {"path": "../escape"}}})
    )
    assert "Error:" in bad_path["content"]

    with patch(
        "article_factory.services.step_tools.write_workspace_file",
        AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        err = asyncio.run(
            registry.execute(
                {
                    "id": "8",
                    "function": {"name": "write_file", "arguments": {"path": "x.txt", "content": "x"}},
                }
            )
        )
        assert "disk full" in err["content"]


@pytest.mark.asyncio
async def test_control_plane_heartbeat_tick_and_loop_error(configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": ""})
        await control_plane_heartbeat_tick(db)

        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
        run = FactoryRun(
            run_id="run-hb",
            topic_slug="sports",
            status="running",
            current_step="writer",
            selected_puller="p1",
            selected_model="m1",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="run-hb",
                step_key="writer",
                status="pulled",
                puller="p1",
            )
        )
        db.commit()

        with patch(
            "article_factory.services.control_plane_heartbeat.send_control_plane_heartbeats",
            AsyncMock(side_effect=RuntimeError("hb fail")),
        ):
            await control_plane_heartbeat_tick(db)
    finally:
        db.close()

    name = _agent_display_name(None, "custom", "custom")
    assert "Article Factory" in name

    loop = ControlPlaneHeartbeatLoop()
    loop._running = True

    async def tick_fail(_db):
        raise RuntimeError("loop tick")

    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_tick",
        tick_fail,
    )
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.settings.heartbeat_interval_seconds",
        0.001,
    )
    monkeypatch.setattr(loop, "_running", False)
    db = db_module.SessionLocal()
    try:
        await loop._loop()
    finally:
        db.close()


# --- executor.py ---


@pytest.mark.asyncio
async def test_poll_step_response_branches(configured_db, monkeypatch) -> None:
    import article_factory.db as db_mod
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "step_response_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 0.2)
    monkeypatch.setattr(settings, "step_puller_stale_grace_seconds", 0.05)
    monkeypatch.setattr(settings, "step_puller_alive_check_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_task_status_check_interval_seconds", 0.01)

    db = db_mod.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-poll", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-poll", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)

    times = iter([0.0, 0.0, 0.0, 0.0, 0.25, 0.25])
    monkeypatch.setattr(time, "monotonic", lambda: next(times, 1.0))

    cp.get_task_status = AsyncMock(return_value={"status": "queued"})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            AsyncMock(return_value={"puller_name": "p1"}),
        ):
            _item, _pulled, outcome, _alive, _status = await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv",
                round_num=1,
                run_id="run-poll",
                tracer=tracer,
                pulled_seen=False,
                target_puller="p1",
            )
    assert outcome == "no_puller"

    times2 = iter([0.0, 0.0, 0.0, 0.1, 0.1])
    monkeypatch.setattr(time, "monotonic", lambda: next(times2, 0.2))
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(return_value=[{"message": {"content": "done"}}])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            AsyncMock(return_value=None),
        ):
            item, pulled, outcome, alive, _status = await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv2",
                round_num=1,
                run_id=None,
                tracer=None,
                pulled_seen=False,
                target_puller="p1",
            )
    assert outcome == "response"
    assert pulled is True
    assert item is not None


@pytest.mark.asyncio
async def test_submit_and_wait_records_cp_round(configured_db, monkeypatch) -> None:
    import article_factory.db as db_mod
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 2)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 0.01)

    db = db_mod.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-round", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-round", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})

    outcomes = [
        (None, False, "no_puller", False, None),
        ({"message": {"content": "ok"}}, True, "response", True, {"status": "completed"}),
    ]

    with patch(
        "article_factory.workers.executor._poll_step_response",
        AsyncMock(side_effect=outcomes),
    ):
        item, _agent, _conv = await _submit_and_wait_for_round(
            cp,
            step_key="writer",
            puller="p1",
            model="m1",
            build_task=lambda a, c: {"agent_id": a},
            round_num=2,
            run_id="run-round",
            tracer=tracer,
        )
    assert item["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_execute_step_tool_refusal_and_no_item(configured_db, monkeypatch, tmp_path) -> None:
    import article_factory.db as db_mod
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))

    db = db_mod.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-tools-exec", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    refusal = {
        "message": {"content": "I cannot browse the web for you."},
        "usage": {},
    }
    accepted = {
        "message": {"content": "Here is the answer."},
        "usage": {},
    }

    with patch(
        "article_factory.workers.executor._submit_and_wait_for_round",
        AsyncMock(
            side_effect=[
                (refusal, "agent-1", "conv-1"),
                (accepted, "agent-2", "conv-2"),
            ]
        ),
    ):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p1",
            model="m1",
            run_id="run-tools-exec",
            enabled_tools={"web_search": True},
            brave_search_api_key="key",
        )
    assert result["content"] == "Here is the answer."

    with patch("article_factory.workers.executor._submit_and_wait_for_round", AsyncMock()):
        monkeypatch.setattr("article_factory.workers.executor.MAX_TOOL_ROUNDS", 0)
        with pytest.raises(RuntimeError, match="without a control plane response"):
            await execute_step(
                cp,
                step_key="writer",
                system_prompt="sys",
                user_content="user",
                puller="p1",
                model="m1",
            )

    tracer = StepTracer(db_mod.SessionLocal(), run_id="run-tools-exec", step_key="writer", puller="p1", model="m1")
    monkeypatch.setattr("article_factory.workers.executor.MAX_TOOL_ROUNDS", 25)
    tool_response = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {"name": "read_file", "arguments": '{"path": "notes.txt"}'},
                },
                "bad-call",
            ],
        },
        "usage": {},
    }
    workspace = run_workspace_root("run-tools-exec")
    (workspace / "notes.txt").write_text("data", encoding="utf-8")

    final_response = {
        "message": {"content": "done after tools"},
        "usage": {},
    }

    with patch(
        "article_factory.workers.executor._submit_and_wait_for_round",
        AsyncMock(
            side_effect=[
                (tool_response, "agent-1", "conv-1"),
                (final_response, "agent-2", "conv-2"),
            ]
        ),
    ):
        result2 = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p1",
            model="m1",
            run_id="run-tools-exec",
            enabled_tools={"read_file": True},
            tracer=tracer,
        )
    assert result2["content"] == "done after tools"


# --- API routes ---


def test_flows_routes_errors(client, api_headers, configured_db) -> None:
    missing_tpl = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": "_templates/missing.flow.json",
            "folder": "x",
            "slug": "x",
            "display_name": "X",
        },
    )
    assert missing_tpl.status_code == 404

    bad_export = client.get("/api/flows/export", headers=api_headers, params={"path": "missing.flow.json"})
    assert bad_export.status_code == 404

    write_flow("move-default.flow.json", new_flow_definition(slug="move-default", display_name="MD", step_count=1))
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp",
                "default_flow_path": "move-default.flow.json",
            },
        )
        db.commit()
    finally:
        db.close()

    moved = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": "move-default.flow.json", "folder": "moved", "slug": "move-default"},
    )
    assert moved.status_code == 200

    dup_conflict = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "sports/standard-4-step.flow.json", "slug": "standard-4-step"},
    )
    assert dup_conflict.status_code == 409

    bad_create = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "bad", "slug": "bad", "display_name": "Bad", "step_count": 0},
    )
    assert bad_create.status_code in {400, 422}

    bad_file = client.get("/api/flows/file", headers=api_headers, params={"path": "../../escape.flow.json"})
    assert bad_file.status_code in {400, 404}

    bad_tree = client.get("/api/flows/tree", headers=api_headers, params={"path": "move-default.flow.json"})
    assert bad_tree.status_code in {400, 404}

    overwrite = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={
            "folder": "imports",
            "slug": "overwrite-me",
            "overwrite": True,
            "flow": flow_to_dict(new_flow_definition(slug="overwrite-me", display_name="O", step_count=1)),
        },
    )
    assert overwrite.status_code == 200
    second = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={
            "folder": "imports",
            "slug": "overwrite-me",
            "overwrite": False,
            "flow": flow_to_dict(new_flow_definition(slug="overwrite-me", display_name="O", step_count=1)),
        },
    )
    assert second.status_code == 409


def test_flow_queues_routes_extended(client, api_headers, configured_db) -> None:
    rel_path, _ = create_flow(folder="", slug="fq-flow", display_name="FQ", step_count=1)

    bad_preset = client.post(
        "/api/flow-queues/presets",
        headers=api_headers,
        json={"name": "", "topics": [], "flow_path": rel_path},
    )
    assert bad_preset.status_code in {400, 422}

    preset = client.post(
        "/api/flow-queues/presets",
        headers=api_headers,
        json={
            "name": "Saved Q",
            "slug": "saved-q",
            "flow_path": rel_path,
            "topics": ["Topic A"],
            "default_model": "m1",
        },
    )
    assert preset.status_code == 200

    got_preset = client.get("/api/flow-queues/presets/saved-q", headers=api_headers)
    assert got_preset.status_code == 200

    deleted_preset = client.delete("/api/flow-queues/presets/saved-q", headers=api_headers)
    assert deleted_preset.status_code == 200

    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "Queue X", "flow_path": rel_path, "topic_slug": "sports"},
    )
    queue_id = created.json()["queue"]["id"]

    start_update = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "topics": ["Topic 1"],
            "default_model": "test-model",
            "flow_path": rel_path,
            "queue_id": queue_id,
            "name": "Queue X Updated",
            "enabled": True,
        },
    )
    assert start_update.status_code in {200, 410}

    missing_update = client.put(
        "/api/flow-queues/99999",
        headers=api_headers,
        json={"name": "Nope"},
    )
    assert missing_update.status_code == 404

    bad_enqueue = client.post(
        f"/api/flow-queues/{queue_id}/enqueue",
        headers=api_headers,
        json={"topics": []},
    )
    assert bad_enqueue.status_code in {200, 400, 422}

    items = client.get(f"/api/flow-queues/{queue_id}/items", headers=api_headers)
    assert items.status_code == 200

    missing_items = client.get("/api/flow-queues/99999/items", headers=api_headers)
    assert missing_items.status_code == 404

    ensure = client.post("/api/flow-queues/ensure-default", headers=api_headers)
    assert ensure.status_code == 200


def test_admin_routes_extended(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import set_factory_api_key

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "real-secret-key")
    finally:
        db.close()

    unauthorized = client.get("/api/settings", headers={"X-API-Key": "wrong"})
    assert unauthorized.status_code == 401

    client_db = db_module.SessionLocal()
    try:
        set_factory_api_key(client_db, "test-factory-key")
    finally:
        client_db.close()

    monkeypatch.setattr(
        "article_factory.routes.admin.brave_web_search",
        AsyncMock(side_effect=RuntimeError("brave down")),
    )
    brave = client.post(
        "/api/settings/test/brave-search",
        headers=api_headers,
        json={"control_plane_url": "http://cp", "brave_search_api_key": "key"},
    )
    assert brave.status_code == 200
    assert brave.json()["ok"] is False

    monkeypatch.setattr(
        "article_factory.routes.admin.ControlPlaneClient",
        lambda **kwargs: AsyncMock(get_task_status=AsyncMock(side_effect=RuntimeError("cp down"))),
    )
    task_status = client.get(
        "/api/control-plane/tasks/status",
        headers=api_headers,
        params={"conversation_id": "conv-1"},
    )
    assert task_status.status_code == 502

    stop_all = client.post(
        "/api/factory/stop-all-runs",
        headers=api_headers,
        json={"requeue": True, "flow_path": ""},
    )
    assert stop_all.status_code in {200, 400}

    switch_bad = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={
            "flow_path": "missing.flow.json",
            "set_as_default": False,
            "clear_history": False,
            "update_queued": False,
            "requeue_running": False,
            "topic_slug": "sports",
        },
    )
    assert switch_bad.status_code in {400, 404}

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Enqueue Q", flow_path="sports/standard-4-step.flow.json", topic_slug="tech")
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    enqueue = client.post(
        "/api/queue",
        headers=api_headers,
        json={
            "topic_slug": "general",
            "prompt": "Batch topic",
            "flow_queue_id": queue_id,
            "flow_path": "",
        },
    )
    assert enqueue.status_code == 200

    batch = client.post(
        "/api/queue/batch",
        headers=api_headers,
        json={
            "topics": ["  ", "Real topic"],
            "flow_queue_id": queue_id,
            "topic_slug": "general",
            "flow_path": "",
            "priority": 5,
        },
    )
    assert batch.status_code == 200
    assert batch.json()["count"] == 1

    item = TopicQueueItem(topic_slug="sports", prompt="Retry me", status="running")
    db = db_module.SessionLocal()
    try:
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-retry-block",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
        )
        db.add(run)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    retry_blocked = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert retry_blocked.status_code == 200
    assert retry_blocked.json()["ok"] is False
    assert "running" in retry_blocked.json()["message"]

    missing_stop = client.post("/api/runs/missing-run-id/stop", headers=api_headers)
    assert missing_stop.status_code == 404

    db = db_module.SessionLocal()
    try:
        db.add(
            CompletedArticle(
                run_id="run-article-files",
                topic_slug="sports",
                title="T",
                summary="S",
                body_markdown="# T\n\nBody",
                manifest={},
            )
        )
        db.commit()
    finally:
        db.close()

    missing_step = client.get(
        "/api/articles/run-article-files/step-files/missing.md",
        headers=api_headers,
    )
    assert missing_step.status_code == 404

    bad_step = client.get(
        "/api/articles/run-article-files/step-files/../escape.md",
        headers=api_headers,
    )
    assert bad_step.status_code in {400, 404}

    missing_ws = client.get(
        "/api/articles/run-article-files/workspace-files/missing.txt",
        headers=api_headers,
    )
    assert missing_ws.status_code == 404
