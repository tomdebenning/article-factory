from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import article_factory.db as db_module
from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import CompletedArticle, FactoryRun, Persona, StepExecution, TopicQueueItem
from article_factory.orchestrator.runner import FactoryLoop, _execute_pipeline
from article_factory.services.flow_schema import flow_to_dict, new_flow_definition
from article_factory.services.flow_storage import create_flow, duplicate_flow, move_flow, write_flow
from article_factory.services.personas import create_persona, delete_persona, slugify_persona_name, update_persona
from article_factory.services.queue_presets import migrate_file_presets_to_db, parse_topics_csv
from article_factory.services.run_recovery import commit_with_retry, ensure_run_pipeline_state
from article_factory.services.step_tools import WorkspaceViolation, resolve_workspace_path, run_workspace_root
from article_factory.services.control_plane_heartbeat import _agent_display_name, control_plane_heartbeat_tick


@pytest.mark.asyncio
async def test_factory_loop_ensure_running_active_task(configured_db) -> None:
    loop = FactoryLoop()
    loop._task = asyncio.create_task(asyncio.sleep(60))
    await loop.ensure_running()
    loop._task.cancel()
    await asyncio.gather(loop._task, return_exceptions=True)


@pytest.mark.asyncio
async def test_factory_loop_dispatch_stale_and_empty_puller(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("stale-p")

    idle_calls = {"n": 0}

    def fake_idle(pullers, model, exclude=None):
        idle_calls["n"] += 1
        if idle_calls["n"] == 1:
            return []
        return [{"puller_name": "", "is_active": True, "status": "idle", "supported_models": ["m1"]}]

    monkeypatch.setattr("article_factory.orchestrator.runner.idle_pullers_for_model", fake_idle)
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(return_value=[{"puller_name": "p1", "supported_models": ["m1"]}])
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", AsyncMock())

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "m1"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued", status="queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()


def test_ensure_run_pipeline_state_reconstructs(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-restore",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="run-restore",
                step_key="writer",
                status="completed",
                response_content="# Title\n\nBody",
            )
        )
        db.commit()
        assert ensure_run_pipeline_state(db, run) is True
        assert run.pipeline_state is not None
    finally:
        db.close()


def test_personas_service_and_routes(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(Persona(slug="taken", name="Taken", style_prompt="Style"))
        db.commit()
    finally:
        db.close()

    assert slugify_persona_name("!!!") == "persona"

    bad_create = client.post(
        "/api/personas",
        headers=api_headers,
        json={"name": "", "style_prompt": "x"},
    )
    assert bad_create.status_code in {400, 422}

    created = client.post(
        "/api/personas",
        headers=api_headers,
        json={"name": "Taken", "style_prompt": "Another style"},
    )
    assert created.status_code == 200
    assert created.json()["persona"]["slug"].startswith("taken")

    missing_put = client.put("/api/personas/missing-slug", headers=api_headers, json={"name": "X", "style_prompt": "Y"})
    assert missing_put.status_code == 404

    bad_put = client.put(
        "/api/personas/taken",
        headers=api_headers,
        json={"name": "Taken", "style_prompt": "   "},
    )
    assert bad_put.status_code == 400

    missing_delete = client.delete("/api/personas/missing-slug", headers=api_headers)
    assert missing_delete.status_code == 404


def test_flows_remaining_routes(client, api_headers, configured_db) -> None:
    rel_path, _ = create_flow(folder="finish", slug="finish-src", display_name="Finish", step_count=1)

    bad_dup = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "missing.flow.json"},
    )
    assert bad_dup.status_code == 404

    dest_path, _ = create_flow(folder="finish-dest", slug="finish-dest", display_name="Dest", step_count=1)
    move_conflict = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": rel_path, "folder": "finish-dest", "slug": "finish-dest"},
    )
    assert move_conflict.status_code == 409

    bad_create = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "finish", "slug": "bad", "display_name": "Bad", "step_count": 99},
    )
    assert bad_create.status_code in {400, 422}

    client.post("/api/flows/folders", headers=api_headers, json={"path": "finish-empty"})
    missing_folder = client.delete("/api/flows/folders", headers=api_headers, params={"path": "finish-empty"})
    assert missing_folder.status_code == 200


def test_flow_queues_preset_value_error(client, api_headers) -> None:
    bad_preset = client.post(
        "/api/flow-queues/presets",
        headers=api_headers,
        json={"name": "No path", "flow_path": "", "topics": []},
    )
    assert bad_preset.status_code in {400, 422}

    rel_path, _ = create_flow(folder="", slug="fq-finish", display_name="FQ", step_count=1)
    bad_start = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "name": "",
            "topics": ["One"],
            "default_model": "m1",
            "flow_path": rel_path,
            "queue_id": 99999,
            "enabled": False,
        },
    )
    assert bad_start.status_code in {404, 410}

    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "Put Q", "flow_path": rel_path},
    )
    queue_id = created.json()["queue"]["id"]
    bad_put = client.put(
        f"/api/flow-queues/{queue_id}",
        headers=api_headers,
        json={"name": "   "},
    )
    assert bad_put.status_code == 400


def test_admin_remaining_routes(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import set_factory_api_key, update_factory_settings

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "real-secret")
        update_factory_settings(db, {"control_plane_url": "http://cp", "default_model": "m1"})
        db.add(
            CompletedArticle(
                run_id="run-article-val",
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

    unauthorized = client.get("/api/settings", headers={"X-API-Key": "bad"})
    assert unauthorized.status_code == 401

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "test-factory-key")
    finally:
        db.close()

    bad_step = client.get(
        "/api/articles/run-article-val/step-files/../escape.md",
        headers=api_headers,
    )
    assert bad_step.status_code in {400, 404}

    bad_ws = client.get(
        "/api/articles/run-article-val/workspace-files/../escape.txt",
        headers=api_headers,
    )
    assert bad_ws.status_code in {400, 404}

    item = TopicQueueItem(topic_slug="sports", prompt="Queued item", status="queued")
    db = db_module.SessionLocal()
    try:
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    not_rerunnable = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert not_rerunnable.status_code == 200
    assert not_rerunnable.json()["ok"] is False


def test_step_tools_escape_and_parse() -> None:
    from article_factory.services.step_tools import _parse_tool_arguments

    assert _parse_tool_arguments(123) == {}

    workspace = run_workspace_root("run-escape")
    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path(workspace, "../outside")


def test_queue_presets_migrate_and_csv(configured_db, tmp_path, monkeypatch) -> None:
    assert parse_topics_csv(",\n") == []

    root = tmp_path / "qp"
    root.mkdir()
    preset = {
        "name": "Migrated",
        "slug": "migrated",
        "flow_path": "sports/standard-4-step.flow.json",
        "topics": ["One"],
    }
    import json

    (root / "migrated.queue.json").write_text(json.dumps(preset), encoding="utf-8")
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: root)

    db = db_module.SessionLocal()
    try:
        count = migrate_file_presets_to_db(db)
        assert count == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_control_plane_heartbeat_active_run(configured_db) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
        run = FactoryRun(
            run_id="run-hb-finish",
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
                run_id="run-hb-finish",
                step_key="writer",
                status="pulled",
                puller="p1",
            )
        )
        db.commit()

        with patch(
            "article_factory.services.control_plane_heartbeat.send_control_plane_heartbeats",
            AsyncMock(),
        ):
            await control_plane_heartbeat_tick(db)
    finally:
        db.close()

    assert _agent_display_name(None, "writer", "writer") == "Article Factory — Writer"


def test_app_startup_with_running_run(configured_db, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from article_factory.app import create_app

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("article_factory.orchestrator.runner.factory_loop.start", noop)
    monkeypatch.setattr("article_factory.orchestrator.runner.factory_loop.stop", noop)
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_loop.start",
        noop,
    )
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_loop.stop",
        noop,
    )
    monkeypatch.setattr("article_factory.services.showroom_status_sync.showroom_status_loop.start", noop)
    monkeypatch.setattr("article_factory.services.showroom_status_sync.showroom_status_loop.stop", noop)
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(),
    )

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-startup", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    with TestClient(create_app()) as test_client:
        assert test_client.get("/api/health").status_code == 200


def test_admin_resolve_queue_path_and_retry_blockers(client, api_headers, configured_db) -> None:
    from article_factory.routes.admin import _resolve_queue_flow_path

    db = db_module.SessionLocal()
    try:
        from article_factory.services.flow_queues import create_flow_queue

        queue = create_flow_queue(db, name="Resolve Q", flow_path="sports/standard-4-step.flow.json", topic_slug="tech")
        db.commit()
        resolved = _resolve_queue_flow_path(db, "", queue.id)
        assert "standard-4-step" in resolved
        assert _resolve_queue_flow_path(db, "custom.flow.json", queue.id) == resolved
    finally:
        db.close()

    item = TopicQueueItem(topic_slug="sports", prompt="Retry blocked", status="failed")
    db = db_module.SessionLocal()
    try:
        db.add(item)
        db.flush()
        db.add(FactoryRun(run_id="run-retry-block2", topic_slug="sports", queue_item_id=item.id, status="failed"))
        db.commit()
        item_id = item.id
    finally:
        db.close()

    blocked = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert blocked.status_code == 200
    assert blocked.json()["ok"] is False
    assert "Fix the items" in blocked.json()["message"]

    bad_step_name = client.get(
        "/api/runs/run-any/step-files/not-valid.txt",
        headers=api_headers,
    )
    assert bad_step_name.status_code == 400


@pytest.mark.asyncio
async def test_token_usage_and_showroom_sync(configured_db, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from article_factory.services.showroom_status_sync import (
        ShowroomStatusLoop,
        refresh_showroom_status,
        schedule_showroom_status_refresh,
        showroom_status_tick,
        sync_showroom_when_factory_busy,
    )
    from article_factory.services.token_usage import (
        enrich_step_record,
        normalize_round_usage,
        serialize_messages_for_token_estimate,
    )

    assert "[user]" in serialize_messages_for_token_estimate([{"role": "user", "content": ""}])

    usage = normalize_round_usage(
        {"input_tokens": 10, "output_tokens": 0, "total_tokens": 5},
        messages=[{"role": "user", "content": "hello"}],
        assistant_message={"content": "world"},
    )
    assert usage["total_tokens"] >= 10

    step = enrich_step_record(
        {
            "step_key": "writer",
            "content": "",
            "tools_used": [{"tool": "web_fetch", "detail": "https://example.com"}],
        },
        selected_model="m1",
    )
    assert step["usage"]["total_tokens"] > 0

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"cms_url": "http://cms.test:8200", "cms_api_key": "key"})
        with patch(
            "article_factory.services.showroom_status_sync.push_showroom_factory_status",
            AsyncMock(side_effect=OperationalError("stmt", {}, Exception("database is locked"))),
        ):
            with patch(
                "article_factory.services.showroom_status_sync.refresh_showroom_status",
                AsyncMock(return_value=True),
            ) as refresh_mock:
                await showroom_status_tick(db)
                refresh_mock.assert_awaited_once()
    finally:
        db.close()

    with patch(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(return_value=True),
    ):
        await sync_showroom_when_factory_busy(active_run_count=1)

    loop = ShowroomStatusLoop()
    loop._running = True
    loop._refresh_event = asyncio.Event()

    async def stop_after_refresh(*_args, **_kwargs):
        loop._running = False
        return True

    monkeypatch.setattr(settings, "heartbeat_interval_seconds", 0.001)
    with patch(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(side_effect=stop_after_refresh),
    ):
        await loop._loop()

    alive_loop = ShowroomStatusLoop()
    alive_loop._running = True
    alive_loop._task = asyncio.create_task(asyncio.sleep(60))
    await alive_loop.start()
    alive_loop._task.cancel()
    await asyncio.gather(alive_loop._task, return_exceptions=True)

    schedule_showroom_status_refresh(force=True)


@pytest.mark.asyncio
async def test_refresh_showroom_operational_error(configured_db, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from article_factory.services.showroom_status_sync import refresh_showroom_status

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"cms_url": "http://cms.test:8200", "cms_api_key": "key"})
        db.commit()
    finally:
        db.close()

    locked = OperationalError("stmt", {}, Exception("database is locked"))
    attempts = {"n": 0}

    async def push_locked(*_args, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise locked

    monkeypatch.setattr("article_factory.services.showroom_status_sync.time.sleep", lambda *_args: None)

    with patch(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        AsyncMock(side_effect=push_locked),
    ):
        result = await refresh_showroom_status(max_attempts=4)
    assert result is True


def test_flow_switch_stop_all_requires_flow_path(configured_db) -> None:
    import asyncio

    from article_factory.services.flow_switch import stop_all_runs

    db = db_module.SessionLocal()
    try:
        with pytest.raises(ValueError, match="flow_path"):
            asyncio.run(stop_all_runs(db, requeue=True, flow_path=None))
    finally:
        db.close()


def test_flow_queues_start_disabled_queue(client, api_headers) -> None:
    rel_path, _ = create_flow(folder="", slug="fq-disabled", display_name="FQ", step_count=1)

    with patch("article_factory.routes.flow_queues.enqueue_topics_to_queue", return_value=[]):
        start = client.post(
            "/api/flow-queues/start",
            headers=api_headers,
            json={
                "name": "Disabled Start",
                "topics": ["Topic"],
                "default_model": "m1",
                "flow_path": rel_path,
                "enabled": False,
            },
        )
    assert start.status_code in {200, 410}
    if start.status_code == 200:
        assert start.json()["queue"]["enabled"] is False

    missing_put = client.put("/api/flow-queues/99999", headers=api_headers, json={"name": "X"})
    assert missing_put.status_code == 404


def test_flows_value_errors(client, api_headers) -> None:
    bad_create = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "v", "slug": "v", "display_name": "V", "step_count": 0},
    )
    assert bad_create.status_code in {400, 422}

    client.post("/api/flows/folders", headers=api_headers, json={"path": "nonempty"})
    client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "nonempty", "slug": "inside", "display_name": "Inside", "step_count": 1},
    )
    nonempty = client.delete("/api/flows/folders", headers=api_headers, params={"path": "nonempty"})
    assert nonempty.status_code == 400

    missing_file = client.delete("/api/flows/file", headers=api_headers, params={"path": "no-such.flow.json"})
    assert missing_file.status_code == 404


def test_queue_retry_assess_message(configured_db) -> None:
    import asyncio

    from article_factory.services.queue_retry import assess_queue_item_retry
    from article_factory.services.runtime_settings import load_runtime_settings

    db = db_module.SessionLocal()
    try:
        runtime = load_runtime_settings(db)
        result = asyncio.run(
            assess_queue_item_retry(
                runtime=runtime,
                loop_running=True,
                active_run=None,
                queue_counts={"queued": 0, "running": 0, "completed": 0, "failed": 0},
            )
        )
        assert "can_retry" in result
        assert result["blockers"] is not None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_executor_poll_queued_activity(configured_db, monkeypatch) -> None:
    from article_factory.services.step_trace import StepTracer
    from article_factory.workers.executor import _poll_step_response

    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 0.15)
    monkeypatch.setattr(settings, "step_puller_alive_check_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_task_status_check_interval_seconds", 0.01)

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-queued-act", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-queued-act", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value={"status": "queued"})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    counter = {"t": 0.0}

    def advancing_monotonic() -> float:
        counter["t"] += 0.05
        return counter["t"]

    monkeypatch.setattr(time, "monotonic", advancing_monotonic)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            AsyncMock(return_value={"puller_name": "p1"}),
        ):
            await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv-q",
                round_num=1,
                run_id="run-queued-act",
                tracer=tracer,
                pulled_seen=False,
                target_puller="p1",
            )


@pytest.mark.asyncio
async def test_factory_loop_dispatch_no_idle_after_stale(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("stale-only")

    monkeypatch.setattr(
        "article_factory.orchestrator.runner.idle_pullers_for_model",
        lambda *_args, **_kwargs: [],
    )
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(return_value=[])
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "m1"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Q", status="queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()


def test_app_lifespan_migrated_presets(configured_db, tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from article_factory.app import create_app

    async def noop(*_args, **_kwargs):
        return None

    for target in (
        "article_factory.orchestrator.runner.factory_loop.start",
        "article_factory.orchestrator.runner.factory_loop.stop",
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_loop.start",
        "article_factory.services.control_plane_heartbeat.control_plane_heartbeat_loop.stop",
        "article_factory.services.showroom_status_sync.showroom_status_loop.start",
        "article_factory.services.showroom_status_sync.showroom_status_loop.stop",
    ):
        monkeypatch.setattr(target, noop)

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "article_factory.app.assess_factory_readiness",
        AsyncMock(return_value={"setup_complete": True, "issue_checks": []}),
    )

    preset_root = tmp_path / "qp-start"
    preset_root.mkdir()
    import json

    (preset_root / "legacy.queue.json").write_text(
        json.dumps(
            {
                "name": "Legacy Startup",
                "slug": "legacy-startup",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": ["One"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: preset_root)

    with TestClient(create_app()) as test_client:
        assert test_client.get("/api/health").status_code == 200
