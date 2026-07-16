from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

import article_factory.db as db_module
import article_factory.orchestrator.runner as runner_module
from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import CompletedArticle, FactoryRun, FlowQueue, StepExecution, TopicQueueItem
from article_factory.orchestrator.flow_runner import execute_flow_pipeline
from article_factory.orchestrator.runner import FactoryLoop, _execute_pipeline, run_pipeline_for_topic
from article_factory.services.control_plane_heartbeat import ControlPlaneHeartbeatLoop, _agent_display_name
from article_factory.services.flow_defaults import build_writer_review_flow
from article_factory.services.flow_queues import (
    create_flow_queue,
    delete_flow_queue,
    enqueue_topics_to_queue,
    flow_queue_payload,
    update_flow_queue,
)
from article_factory.services.flow_schema import FlowDefinition, flow_to_dict, new_flow_definition
from article_factory.services.flow_storage import (
    _flow_catalog_entry,
    create_flow,
    create_flow_from_template,
    is_template_path,
    list_templates,
    move_flow,
    write_flow,
)
from article_factory.services.queue_presets import delete_queue_preset, migrate_file_presets_to_db, parse_topics_csv, write_queue_preset
from article_factory.services.run_control import RunCancelledError, clear_run_cancel
from article_factory.services.run_recovery import commit_with_retry, ensure_run_pipeline_state, reconstruct_pipeline_state
from article_factory.services.step_tools import (
    StepToolRegistry,
    WorkspaceViolation,
    _parse_tool_arguments,
    resolve_workspace_path,
    run_workspace_root,
    tool_use_nudge_message,
)
from article_factory.services.step_trace import enrich_steps_with_responses
from article_factory.workers.executor import _poll_step_response, _submit_and_wait_for_round, execute_step


def _step_record(step_key: str, content: str) -> dict:
    return {
        "step_key": step_key,
        "step_name": step_key,
        "content": content,
        "duration_ms": 3,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture(autouse=True)
async def _clear_cancel_flags():
    yield
    for run_id in ("run-after-step", "run-empty-steps", "run-cancel-nonrun"):
        await clear_run_cancel(run_id)


# --- routes/flows.py ---


def test_flows_api_error_paths(client, api_headers, configured_db) -> None:
    templates = client.get("/api/flows/templates", headers=api_headers).json()["templates"]
    template_path = templates[0]["path"]
    client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": template_path,
            "folder": "push-dup",
            "slug": "dup-flow",
            "display_name": "Dup",
        },
    )
    exists = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": template_path,
            "folder": "push-dup",
            "slug": "dup-flow",
            "display_name": "Dup Again",
        },
    )
    assert exists.status_code == 409

    bad_tpl = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": "sports/standard-4-step.flow.json",
            "folder": "x",
            "slug": "bad",
            "display_name": "Bad",
        },
    )
    assert bad_tpl.status_code == 400

    escape = client.get("/api/flows/export", headers=api_headers, params={"path": "../../x.flow.json"})
    assert escape.status_code in {400, 404}

    rel_path, _ = create_flow(folder="push-src", slug="push-src", display_name="Src", step_count=1)
    dup = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": rel_path, "slug": "push-src"},
    )
    assert dup.status_code == 409

    missing_dup = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "missing.flow.json"},
    )
    assert missing_dup.status_code == 404

    move_missing = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": "missing.flow.json", "folder": "dest"},
    )
    assert move_missing.status_code == 404

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp",
                "default_flow_path": rel_path,
            },
        )
        item = TopicQueueItem(
            topic_slug="sports",
            prompt="Queued",
            status="queued",
            flow_path=rel_path,
        )
        db.add(item)
        db.commit()
    finally:
        db.close()

    moved = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": rel_path, "folder": "push-dest", "slug": "moved-flow"},
    )
    assert moved.status_code == 200

    bad_tree = client.get("/api/flows/tree", headers=api_headers, params={"path": "push-dest/moved-flow.flow.json"})
    assert bad_tree.status_code in {400, 404}

    bad_file = client.get("/api/flows/file", headers=api_headers, params={"path": "missing.flow.json"})
    assert bad_file.status_code == 404

    bad_put = client.put(
        "/api/flows/file",
        headers=api_headers,
        params={"path": "../../escape.flow.json"},
        json={"flow": flow_to_dict(new_flow_definition(slug="x", display_name="X", step_count=1))},
    )
    assert bad_put.status_code in {400, 404}

    bad_folder = client.post("/api/flows/folders", headers=api_headers, json={"path": "../escape"})
    assert bad_folder.status_code in {400, 409}

    nonempty = client.delete("/api/flows/folders", headers=api_headers, params={"path": "push-src"})
    assert nonempty.status_code in {200, 400}

    delete_missing = client.delete("/api/flows/file", headers=api_headers, params={"path": "missing.flow.json"})
    assert delete_missing.status_code == 404


# --- orchestrator/runner.py ---


@pytest.mark.asyncio
async def test_execute_pipeline_resume_by_step_id(configured_db, monkeypatch) -> None:
    from article_factory.services.flow_storage import read_flow

    rel_path = "sports/standard-4-step.flow.json"
    flow = read_flow(rel_path)
    writer_id = next(s.step_id for s in flow.steps if s.step_key == "writer")
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
            run_id="run-resume-id",
            topic_slug="sports",
            flow_path=rel_path,
            status="running",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()
        await _execute_pipeline(db, run=run, topic_prompt="Topic", resume_from_step=writer_id)
        assert captured["resume_from_step_id"] == writer_id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execute_pipeline_asyncio_cancelled_non_running(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-cancel-nonrun",
            topic_slug="sports",
            status="cancelled",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()

        async def boom(*args, **kwargs):
            raise asyncio.CancelledError()

        monkeypatch.setattr("article_factory.orchestrator.runner.execute_flow_pipeline", boom)
        fail_mock = MagicMock()
        monkeypatch.setattr("article_factory.orchestrator.runner.fail_in_flight_steps", fail_mock)

        with pytest.raises(asyncio.CancelledError):
            await _execute_pipeline(db, run=run, topic_prompt="Topic")
        fail_mock.assert_called_once()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_snapshot_id(configured_db, monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.orchestrator.runner._execute_pipeline",
        AsyncMock(side_effect=lambda db, *, run, topic_prompt, resume_from_step=None: run),
    )
    monkeypatch.setattr("article_factory.orchestrator.runner._emit_run_event", AsyncMock())
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.ensure_flow_version_for_run",
        lambda _db, _path: MagicMock(id=1),
    )

    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Snap", flow_path="sports/standard-4-step.flow.json")
        item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug="sports",
            prompt="Snap",
            status="queued",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(item)
        db.flush()
        run = await run_pipeline_for_topic(
            db,
            topic_slug="sports",
            topic_prompt="Snap",
            queue_item_id=item.id,
            flow_path="sports/standard-4-step.flow.json",
        )
        assert run.topic_queue_snapshot_id is not None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_dispatch_branches(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True
    loop._reserved_pullers.add("stale")

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {"puller_name": "", "is_active": True, "status": "idle", "supported_models": ["m1"]},
            {"puller_name": "p1", "is_active": True, "status": "idle", "supported_models": ["m1"]},
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", AsyncMock())
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.ensure_run_pipeline_state",
        lambda _db, _run: False,
    )

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "m1"},
        )
        run = FactoryRun(run_id="run-dispatch-519", topic_slug="sports", status="running", current_step="writer")
        db.add(run)
        db.add(TopicQueueItem(topic_slug="sports", prompt="Q", status="queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()

    done = asyncio.create_task(asyncio.sleep(0))
    await done
    loop._task = done
    monkeypatch.setattr(loop, "_loop", AsyncMock())
    await loop.ensure_running()

    async def bad_coro():
        raise RuntimeError("worker failed")

    loop._spawn_worker("bad-worker", bad_coro())
    await asyncio.sleep(0.05)

    loop._running = True
    monkeypatch.setattr(loop, "_dispatch_tick", AsyncMock())
    monkeypatch.setattr(loop, "_wait_for_next_tick", AsyncMock(side_effect=lambda: setattr(loop, "_running", False)))
    await loop._loop()
    await loop._wait_for_next_tick()


# --- flow_runner.py ---


@pytest.mark.asyncio
async def test_execute_flow_cancelled_after_step(configured_db, monkeypatch) -> None:
    rel_path = "test/cancel-after-push.flow.json"
    write_flow(rel_path, build_writer_review_flow())

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        db = db_module.SessionLocal()
        try:
            run = db.query(FactoryRun).filter_by(run_id="run-after-step").one()
            run.status = "cancelled"
            db.commit()
        finally:
            db.close()
        return _step_record("writer", "# Title\n\nBody")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "m1"})
        run = FactoryRun(run_id="run-after-step", topic_slug="sports", flow_path=rel_path, status="running")
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
async def test_execute_flow_empty_steps_and_max_iterations(configured_db, monkeypatch) -> None:
    empty = FlowDefinition.model_construct(
        slug="empty",
        display_name="Empty",
        max_iterations=5,
        article_step_id="x",
        steps=[],
    )
    rel_empty = "test/empty-push.flow.json"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.read_flow", lambda p: empty)
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", AsyncMock(return_value="p1"))
    monkeypatch.setattr(
        "article_factory.orchestrator.flow_runner.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

        update_factory_settings(db, {"default_model": "m1"})
        run = FactoryRun(run_id="run-empty-steps", topic_slug="sports", flow_path=rel_empty, status="running")
        db.add(run)
        db.commit()
        runtime = load_runtime_settings(db)
        result = await execute_flow_pipeline(
            db,
            run=run,
            flow_path=rel_empty,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert "without completion" in (result.error or "").lower()

        rel_path = "test/max-iter-push.flow.json"
        write_flow(rel_path, build_writer_review_flow())
        flow = build_writer_review_flow()
        flow.max_iterations = 1

        review_calls = {"n": 0}

        async def fake_step(ctx, cp=None, tracer=None, run_id=None):
            if ctx.step_key == "writer":
                return _step_record("writer", "# Title\n\nBody")
            review_calls["n"] += 1
            return _step_record("review", "Fix.\n\nVERDICT: REJECT")

        monkeypatch.setattr(
            "article_factory.orchestrator.flow_runner.read_flow",
            lambda p: flow if "max-iter" in p else empty,
        )
        monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

        run2 = FactoryRun(run_id="run-max-iter", topic_slug="sports", flow_path=rel_path, status="running")
        db.add(run2)
        db.commit()
        result2 = await execute_flow_pipeline(
            db,
            run=run2,
            flow_path=rel_path,
            topic_prompt="Topic",
            runtime=runtime,
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=AsyncMock(),
        )
        assert "Max flow iterations" in (result2.error or "")
    finally:
        db.close()


# --- flow_storage.py ---


def test_flow_storage_helpers(configured_db, tmp_path, monkeypatch) -> None:
    assert is_template_path("_templates/foo.flow.json") is True
    assert is_template_path("_templates") is True
    assert is_template_path("sports/x.flow.json") is False

    assert _flow_catalog_entry({"path": ""}) is None

    flows_dir = (tmp_path / "fs-push").resolve()
    flows_dir.mkdir()
    monkeypatch.setattr(settings, "flows_root", str(flows_dir))
    monkeypatch.setattr("article_factory.services.flow_storage.flows_root", lambda: flows_dir)

    bad = flows_dir / "bad.flow.json"
    bad.write_text('["not", "object"]', encoding="utf-8")
    entry = _flow_catalog_entry({"path": "bad.flow.json", "name": "bad.flow.json"})
    assert entry is not None
    assert entry["step_count"] == 0

    rel_path, _ = create_flow(folder="slug-move", slug="slug-move", display_name="Slug", step_count=1)
    with pytest.raises(ValueError, match="slug"):
        move_flow(rel_path, folder="dest", slug="   ")


def test_flow_storage_template_exists(configured_db) -> None:
    create_flow(folder="tpl-dup", slug="tpl-dup-flow", display_name="Dup", step_count=1)
    templates = list_templates()
    template_path = templates[0]["path"]
    with pytest.raises(FileExistsError):
        create_flow_from_template(
            template_path=template_path,
            folder="tpl-dup",
            slug="tpl-dup-flow",
            display_name="Exists",
        )


# --- services ---


def test_flow_queues_service_edges(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        q1 = create_flow_queue(db, name="Queue One", flow_path="sports/standard-4-step.flow.json")
        q2 = create_flow_queue(db, name="Queue One", flow_path="sports/standard-4-step.flow.json")
        assert q2.slug.endswith("-2")

        item = TopicQueueItem(
            flow_queue_id=q1.id,
            topic_slug="sports",
            prompt="Running",
            status="running",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-fq-active",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
            )
        )
        db.commit()
        payload = flow_queue_payload(db, q1)
        assert payload["active_run_id"] == "run-fq-active"

        update_flow_queue(db, q1.id, dispatch_order=99)
        with pytest.raises(ValueError, match="Stop active"):
            delete_flow_queue(db, q1.id)

        created = enqueue_topics_to_queue(db, q1.id, ["A", "  ", "B"])
        assert len(created) == 2
    finally:
        db.close()


def test_run_recovery_edges(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        attempts = {"n": 0}

        def commit():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise OperationalError("stmt", {}, Exception("database is locked"))

        db.commit = commit  # type: ignore[method-assign]
        db.rollback = MagicMock()
        monkeypatch.setattr("article_factory.services.run_recovery.time.sleep", lambda _s: None)
        commit_with_retry(db, max_attempts=3, base_delay=0.01)

        run = FactoryRun(
            run_id="run-recon",
            topic_slug="sports",
            status="running",
            current_step="unknown_step",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run)
        db.commit()
        assert reconstruct_pipeline_state(db, run) is None

        run2 = FactoryRun(
            run_id="run-recon2",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run2)
        db.flush()
        db.add(StepExecution(run_id="run-recon2", step_key="writer", status="submitted"))
        db.commit()
        assert reconstruct_pipeline_state(db, run2) is None

        run3 = FactoryRun(
            run_id="run-ensure",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run3)
        db.commit()
        assert ensure_run_pipeline_state(db, run3) is False
    finally:
        db.close()


def test_queue_presets_edges(configured_db, tmp_path, monkeypatch) -> None:
    assert parse_topics_csv("a\n,\nb") == ["a", "b"]

    db = db_module.SessionLocal()
    try:
        write_queue_preset(
            db,
            {
                "name": "To Delete",
                "slug": "to-delete",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": ["One"],
            },
        )
        db.flush()
        deleted = delete_queue_preset(db, "to-delete")
        assert deleted["slug"] == "to-delete"
    finally:
        db.close()

    root = tmp_path / "presets"
    root.mkdir()
    (root / "bad.queue.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: root)
    db = db_module.SessionLocal()
    try:
        assert migrate_file_presets_to_db(db) == 0
    finally:
        db.close()


def test_step_tools_edges(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    workspace = run_workspace_root("run-st-edge")

    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path(workspace, "")

    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path(workspace, "/abs")

    assert _parse_tool_arguments('["list"]') == {}
    nudge = tool_use_nudge_message({"write_file": True, "read_file": True})
    assert "write_file" in nudge

    big = b"x" * (101 * 1024)
    big_file = workspace / "big.txt"
    big_file.write_bytes(big)
    import asyncio
    from article_factory.services.step_tools import read_workspace_file

    text = asyncio.run(read_workspace_file(big_file, display_path="big.txt"))
    assert "truncated" in text

    registry = StepToolRegistry(workspace_root=workspace, brave_api_key="key")
    wrote = asyncio.run(
        registry.execute(
            {
                "id": "1",
                "function": {"name": "write_file", "arguments": {"path": "out.txt", "content": "data"}},
            }
        )
    )
    assert "wrote" in wrote["content"]


def test_step_trace_enrich_manifest_and_pipeline(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-enrich-push",
            topic_slug="sports",
            status="completed",
            pipeline_state={
                "step_records": [
                    {"step_key": "writer", "content": "Body", "duration_ms": 5},
                    {"step_key": "writer", "content": "Body2"},
                ]
            },
            manifest={
                "steps": [
                    {"step_key": "writer", "status": "completed", "duration_ms": 1, "usage": {"total_tokens": 2}},
                    {"step_key": "writer", "status": "completed"},
                    {"step_key": "review", "status": "completed"},
                ]
            },
        )
        db.add(run)
        db.commit()

        steps = list(run.manifest["steps"])
        enriched = enrich_steps_with_responses(db, "run-enrich-push", steps)
        assert enriched[0]["response_content"] == "Body"
        assert enriched[1]["response_content"] == "Body2"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_control_plane_heartbeat_edges(configured_db) -> None:
    from article_factory.services.control_plane_heartbeat import control_plane_heartbeat_tick
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": ""})
        await control_plane_heartbeat_tick(db)
    finally:
        db.close()

    run = FactoryRun(run_id="r", topic_slug="sports", status="running", flow_path="sports/standard-4-step.flow.json")
    assert _agent_display_name(run, "custom", "custom") == "Article Factory — custom"

    loop = ControlPlaneHeartbeatLoop()
    loop._task = MagicMock()
    loop._task.done = MagicMock(return_value=False)
    await loop.start()

    loop2 = ControlPlaneHeartbeatLoop()
    loop2._running = True

    async def stop_after_sleep(*_args, **_kwargs):
        loop2._running = False

    with patch(
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_tick",
        AsyncMock(side_effect=RuntimeError("tick failed")),
    ):
        with patch(
            "article_factory.services.control_plane_heartbeat.settings.heartbeat_interval_seconds",
            0.001,
        ):
            with patch("article_factory.services.control_plane_heartbeat.asyncio.sleep", side_effect=stop_after_sleep):
                await loop2._loop()


# --- executor ---


@pytest.mark.asyncio
async def test_poll_step_response_more_branches(configured_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "step_response_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 0.15)
    monkeypatch.setattr(settings, "step_puller_stale_grace_seconds", 0.05)
    monkeypatch.setattr(settings, "step_puller_alive_check_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_task_status_check_interval_seconds", 0.01)

    cp = AsyncMock(spec=ControlPlaneClient)
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-poll-push", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-poll-push", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    times = [0.0, 0.0, 0.0, 0.2, 0.2, 0.3]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0) if times else 0.3)
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            AsyncMock(return_value={"puller_name": "p1"}),
        ):
            _item, pulled, outcome, _alive, _status = await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv-fetched",
                round_num=1,
                run_id="run-poll-push",
                tracer=tracer,
                pulled_seen=False,
                target_puller="p1",
            )
    assert pulled is True
    assert outcome == "timeout"

    times2 = [0.0, 0.0, 0.01, 0.07, 0.07]
    monkeypatch.setattr(time, "monotonic", lambda: times2.pop(0) if times2 else 0.1)
    cp.get_task_status = AsyncMock(return_value={"status": ""})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    puller_checks = {"n": 0}

    async def puller_status(*_args, **_kwargs):
        puller_checks["n"] += 1
        return {"puller_name": "p1"} if puller_checks["n"] == 1 else None

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            side_effect=puller_status,
        ):
            _item2, _pulled2, outcome2, _alive2, _status2 = await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv-reconnect",
                round_num=1,
                run_id=None,
                tracer=None,
                pulled_seen=False,
                target_puller="p1",
            )
    assert outcome2 == "no_puller"


@pytest.mark.asyncio
async def test_submit_and_wait_timeout_tracer(configured_db, monkeypatch) -> None:
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-timeout-push", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-timeout-push", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={})

    with patch(
        "article_factory.workers.executor._poll_step_response",
        AsyncMock(return_value=(None, True, "timeout", True, {"status": "fetched"})),
    ):
        with pytest.raises(TimeoutError):
            await _submit_and_wait_for_round(
                cp,
                step_key="writer",
                puller="p1",
                model="m1",
                build_task=lambda a, c: {},
                round_num=2,
                run_id="run-timeout-push",
                tracer=tracer,
            )
    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_execute_step_response_error_tracer(configured_db, monkeypatch) -> None:
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-resp-err", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-resp-err", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    err_item = {"message": {"content": ""}, "error": "puller died", "usage": {}}

    with patch(
        "article_factory.workers.executor._submit_and_wait_for_round",
        AsyncMock(return_value=(err_item, "agent", "conv")),
    ):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p1",
            model="m1",
            tracer=tracer,
        )
    assert result["error"] == "puller died"
    assert tracer.execution.status == "failed"


# --- admin + flow_queues routes ---


def test_admin_and_flow_queue_routes(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import set_factory_api_key, update_factory_settings

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "secret-key-123")
    finally:
        db.close()

    unauthorized = client.get("/api/settings", headers={"X-API-Key": "wrong"})
    assert unauthorized.status_code == 401

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "test-factory-key")
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "m1"},
        )
        queue = create_flow_queue(db, name="Admin Q", flow_path="sports/standard-4-step.flow.json", topic_slug="tech")
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    enqueue = client.post(
        "/api/queue",
        headers=api_headers,
        json={
            "topic_slug": "general",
            "prompt": "From queue",
            "flow_queue_id": queue_id,
            "flow_path": "",
        },
    )
    assert enqueue.status_code == 200

    switch_bad = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={
            "flow_path": "",
            "set_as_default": False,
            "clear_history": False,
            "update_queued": False,
            "requeue_running": False,
            "topic_slug": "sports",
        },
    )
    assert switch_bad.status_code in {400, 404, 422}

    item = TopicQueueItem(topic_slug="sports", prompt="Failed", status="failed")
    db = db_module.SessionLocal()
    try:
        db.add(item)
        db.flush()
        db.add(FactoryRun(run_id="run-retry-push", topic_slug="sports", queue_item_id=item.id, status="failed"))
        db.commit()
        item_id = item.id
    finally:
        db.close()

    blocked = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert blocked.status_code == 200
    assert blocked.json()["ok"] is False

    rel_path, _ = create_flow(folder="", slug="fq-push", display_name="FQ", step_count=1)
    bad_preset = client.get("/api/flow-queues/presets/!!!", headers=api_headers)
    assert bad_preset.status_code in {400, 404}

    missing_preset = client.delete("/api/flow-queues/presets/missing-xyz", headers=api_headers)
    assert missing_preset.status_code == 404

    bad_start = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "name": "Bad",
            "topics": ["One"],
            "default_model": "m1",
            "flow_path": rel_path,
            "queue_id": 99999,
        },
    )
    assert bad_start.status_code == 404

    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "Disabled", "flow_path": rel_path, "enabled": False},
    )
    queue_id2 = created.json()["queue"]["id"]
    client.put(f"/api/flow-queues/{queue_id2}", headers=api_headers, json={"enabled": False})

    bad_enqueue = client.post(
        f"/api/flow-queues/{queue_id2}/enqueue",
        headers=api_headers,
        json={"topics": ["One"]},
    )
    assert bad_enqueue.status_code == 400

    missing_put = client.put("/api/flow-queues/99999", headers=api_headers, json={"name": "X"})
    assert missing_put.status_code == 404

    missing_delete = client.delete("/api/flow-queues/99999", headers=api_headers)
    assert missing_delete.status_code == 404

    missing_enqueue = client.post(
        "/api/flow-queues/99999/enqueue",
        headers=api_headers,
        json={"topics": ["One"]},
    )
    assert missing_enqueue.status_code == 404

    db = db_module.SessionLocal()
    try:
        item2 = TopicQueueItem(topic_slug="sports", prompt="Pub", status="failed")
        db.add(item2)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-pub-push",
                topic_slug="sports",
                queue_item_id=item2.id,
                status="failed",
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-pub-push",
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

    monkeypatch.setattr(
        "article_factory.routes.admin.publish_article_to_showroom",
        AsyncMock(return_value={"ok": True}),
    )
    published = client.post("/api/runs/run-pub-push/publish", headers=api_headers)
    assert published.status_code == 200
    assert published.json()["ok"] is True

    step_404 = client.get("/api/articles/run-pub-push/step-files/missing.md", headers=api_headers)
    assert step_404.status_code == 404

    ws_404 = client.get("/api/articles/run-pub-push/workspace-files/missing.txt", headers=api_headers)
    assert ws_404.status_code == 404
