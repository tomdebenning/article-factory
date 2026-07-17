from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import article_factory.db as db_module
from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem
from article_factory.orchestrator.runner import FactoryLoop
from article_factory.services.flow_storage import create_flow
from article_factory.workers.executor import _poll_step_response, execute_step


@pytest.mark.asyncio
async def test_poll_step_response_queued_reconnect_and_response_wait(configured_db, monkeypatch) -> None:
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 0.2)
    monkeypatch.setattr(settings, "step_response_timeout_seconds", 0.2)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 0.2)
    monkeypatch.setattr(settings, "step_puller_stale_grace_seconds", 0.08)
    monkeypatch.setattr(settings, "step_puller_alive_check_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_task_status_check_interval_seconds", 0.01)

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-poll-last", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-poll-last", step_key="writer", puller="p1", model="m1")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value={"status": "queued"})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    counter = {"t": 0.0}

    def monotonic() -> float:
        counter["t"] += 0.04
        return counter["t"]

    monkeypatch.setattr(time, "monotonic", monotonic)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            AsyncMock(return_value={"puller_name": "p1"}),
        ):
            await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv-queued-last",
                round_num=1,
                run_id="run-poll-last",
                tracer=tracer,
                pulled_seen=False,
                target_puller="p1",
            )

    counter["t"] = 0.0
    cp.get_task_status = AsyncMock(return_value={"status": ""})
    puller_checks = {"n": 0}

    async def puller_reconnect(*_args, **_kwargs):
        puller_checks["n"] += 1
        return {"puller_name": "p1"} if puller_checks["n"] == 1 else None

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            side_effect=puller_reconnect,
        ):
            await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv-reconnect-last",
                round_num=1,
                run_id="run-poll-last",
                tracer=tracer,
                pulled_seen=False,
                target_puller="p1",
            )

    counter["t"] = 0.0
    cp.get_task_status = AsyncMock(return_value={"status": "working"})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.get_registered_puller_on_cp",
            AsyncMock(return_value=None),
        ):
            await _poll_step_response(
                cp,
                agent_id="agent",
                conversation_id="conv-early-wait",
                round_num=1,
                run_id="run-poll-last",
                tracer=tracer,
                pulled_seen=True,
                target_puller="p1",
            )


@pytest.mark.asyncio
async def test_execute_step_no_item_after_zero_rounds(configured_db, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.workers.executor.MAX_TOOL_ROUNDS", 0)
    cp = AsyncMock(spec=ControlPlaneClient)

    with pytest.raises(RuntimeError, match="ended without"):
        await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p1",
            model="m1",
        )


def test_admin_enqueue_default_flow_and_article_step_value(client, api_headers, configured_db) -> None:
    resp = client.post(
        "/api/queue",
        headers=api_headers,
        json={
            "topic_slug": "sports",
            "prompt": "Default flow path",
            "flow_path": "",
            "flow_queue_id": 99999,
        },
    )
    assert resp.status_code == 200

    db = db_module.SessionLocal()
    try:
        item = db.get(TopicQueueItem, resp.json()["id"])
        assert item is not None
        assert item.flow_path
        db.add(
            CompletedArticle(
                run_id="run-step-val-last",
                topic_slug="sports",
                title="T",
                summary="S",
                body_markdown="# T",
                manifest={},
            )
        )
        db.commit()
    finally:
        db.close()

    bad = client.get(
        "/api/articles/run-step-val-last/step-files/evil.txt",
        headers=api_headers,
    )
    assert bad.status_code == 400

    from article_factory.services.run_attachments import read_run_workspace_file

    with pytest.raises(ValueError, match="Invalid workspace path"):
        read_run_workspace_file("run-step-val-last", "..")


def test_flow_queues_preset_value_error_and_post_value_error(client, api_headers) -> None:
    with patch(
        "article_factory.routes.flow_queues.read_queue_preset",
        side_effect=ValueError("bad preset"),
    ):
        bad_preset = client.get("/api/flow-queues/presets/bad-slug", headers=api_headers)
    assert bad_preset.status_code == 400

    rel_path, _ = create_flow(folder="", slug="fq-val", display_name="FQ", step_count=1)
    with patch("article_factory.routes.flow_queues.enqueue_topics_to_queue", return_value=[]):
        bad_start = client.post(
            "/api/flow-queues/start",
            headers=api_headers,
            json={
                "name": "",
                "topics": ["Topic"],
                "default_model": "m1",
                "flow_path": rel_path,
            },
        )
    assert bad_start.status_code in {400, 410}

    bad_post = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "   ", "flow_path": rel_path},
    )
    assert bad_post.status_code == 400


def test_flows_duplicate_escape_and_delete_success(client, api_headers) -> None:
    rel_path, _ = create_flow(folder="", slug="del-me", display_name="Del", step_count=1)

    dup_bad = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "../escape.flow.json"},
    )
    assert dup_bad.status_code == 400

    deleted = client.delete("/api/flows/file", headers=api_headers, params={"path": rel_path})
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


@pytest.mark.asyncio
async def test_factory_loop_dispatch_empty_puller_and_loop_error(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("stale-x")

    idle_calls = {"n": 0}

    def fake_idle(pullers, model, exclude=None):
        idle_calls["n"] += 1
        return [
            {"puller_name": "", "is_active": True, "status": "idle", "supported_models": ["m1"]},
            {"puller_name": "good", "is_active": True, "status": "idle", "supported_models": ["m1"]},
        ]

    monkeypatch.setattr("article_factory.orchestrator.runner.idle_pullers_for_model", fake_idle)
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(return_value=[{"puller_name": "good", "supported_models": ["m1"]}])
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", AsyncMock())

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "m1"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Q2", status="queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()

    loop2 = FactoryLoop()
    loop2._running = True
    monkeypatch.setattr(loop2, "_dispatch_tick", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(loop2, "_wait_for_next_tick", AsyncMock(side_effect=lambda: setattr(loop2, "_running", False)))
    await loop2._loop()

    loop3 = FactoryLoop()
    monkeypatch.setattr(settings, "dispatch_interval_seconds", 0.001)
    event = loop3._ensure_dispatch_event()
    assert not event.is_set()
    await loop3._wait_for_next_tick()


@pytest.mark.asyncio
async def test_showroom_refresh_generic_error_and_tick_exception(configured_db, monkeypatch) -> None:
    from article_factory.services.showroom_status_sync import (
        ShowroomStatusLoop,
        refresh_showroom_status,
        showroom_status_tick,
    )

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"cms_url": "http://cms.test:8200", "cms_api_key": "key"})
        db.commit()
    finally:
        db.close()

    with patch(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        AsyncMock(side_effect=RuntimeError("cms down")),
    ):
        assert await refresh_showroom_status() is False

    db = db_module.SessionLocal()
    try:
        with patch(
            "article_factory.services.showroom_status_sync.push_showroom_factory_status",
            AsyncMock(side_effect=RuntimeError("tick fail")),
        ):
            await showroom_status_tick(db)
    finally:
        db.close()

    loop = ShowroomStatusLoop()
    loop._running = True
    loop._refresh_event = None

    async def noop_refresh():
        loop._running = False

    with patch(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(side_effect=noop_refresh),
    ):
        await loop._loop()


def test_token_usage_total_tokens_adjustment() -> None:
    from article_factory.services.token_usage import enrich_step_record, normalize_round_usage

    usage = normalize_round_usage(
        {"input_tokens": 8, "output_tokens": 4, "total_tokens": 5},
        messages=[{"role": "user", "content": "hello"}],
        assistant_message={"content": "world"},
    )
    assert usage["total_tokens"] == 12

    step = enrich_step_record(
        {
            "step_key": "writer",
            "content": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "tools_used": [{"tool": "web_search", "detail": "query text"}],
        },
        selected_model="m1",
    )
    assert step["usage"]["total_tokens"] > 0


def test_app_startup_setup_complete_log(configured_db, monkeypatch) -> None:
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

    with TestClient(create_app()) as test_client:
        assert test_client.get("/api/health").status_code == 200

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-busy-show", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    with patch(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(return_value=True),
    ):
        with TestClient(create_app()) as test_client:
            test_client.get("/api/health")


@pytest.mark.asyncio
async def test_control_plane_client_status_and_activity_fallback() -> None:
    from article_factory.control_plane.client import ControlPlaneClient

    cp = ControlPlaneClient(base_url="http://cp.test:8000")

    with patch("httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.HTTPError("network"))
        client_cls.return_value = client
        assert await cp.get_task_status("conv-1") is None

    with patch.object(cp, "get_task_status", AsyncMock(return_value=None)):
        with patch.object(
            cp,
            "get_activity",
            AsyncMock(return_value=[{"details": {"conversation_id": "conv-act"}}]),
        ):
            assert await cp.task_was_fetched(conversation_id="conv-act") is True


def test_factory_api_key_cache_without_db(monkeypatch) -> None:
    from article_factory.services.factory_api_key_cache import (
        get_cached_factory_api_key,
        invalidate_factory_api_key_cache,
        warm_factory_api_key_cache,
    )

    invalidate_factory_api_key_cache()
    monkeypatch.setattr(settings, "factory_api_key", "")
    assert warm_factory_api_key_cache(db=None) == ""
    invalidate_factory_api_key_cache()
    assert get_cached_factory_api_key() == ""


def test_queue_presets_csv_and_migrate_errors(configured_db, tmp_path, monkeypatch) -> None:
    from article_factory.services.queue_presets import (
        _unique_slug,
        migrate_file_presets_to_db,
        parse_topics_csv,
        write_queue_preset,
    )
    from article_factory.models import SavedQueue

    assert parse_topics_csv("topic1\n\n,,\ntopic2") == ["topic1", "topic2"]

    db = db_module.SessionLocal()
    try:
        first = write_queue_preset(
            db,
            {
                "name": "Second",
                "slug": "second-queue",
                "flow_path": "sports/standard-4-step.flow.json",
                "topics": ["A"],
            },
        )
        row = db.query(SavedQueue).filter_by(slug=first["slug"]).one()
        assert _unique_slug(db, "second-queue", exclude_id=row.id) == "second-queue"
        db.commit()
    finally:
        db.close()

    not_dir = tmp_path / "not-presets"
    not_dir.mkdir()
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: not_dir)
    db = db_module.SessionLocal()
    try:
        assert migrate_file_presets_to_db(db) == 0
    finally:
        db.close()

    preset_root = tmp_path / "migrate-err"
    preset_root.mkdir()
    import json

    bad_file = preset_root / "bad.queue.json"
    bad_file.write_text(
        json.dumps({"name": "Good", "flow_path": "sports/standard-4-step.flow.json", "topics": ["X"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: preset_root)
    def fail_unlink(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.unlink", fail_unlink)

    db = db_module.SessionLocal()
    try:
        imported = migrate_file_presets_to_db(db)
        assert imported == 1
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_flow_switch_requeue_and_clear_history(configured_db) -> None:
    from article_factory.services.flow_switch import clear_factory_history, stop_all_runs, switch_active_flow

    rel_path = "sports/standard-4-step.flow.json"
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-switch-last",
            topic_slug="sports",
            status="running",
            queue_item_id=1,
        )
        db.add(run)
        db.add(TopicQueueItem(topic_slug="sports", prompt="Old", status="queued"))
        db.commit()

        cleared = clear_factory_history(db)
        assert cleared["running_runs_left"] == 1

        with patch("article_factory.services.flow_switch.request_run_cancel", AsyncMock()):
            with patch("article_factory.orchestrator.runner.factory_loop") as mock_loop:
                mock_loop.cancel_run_workers.return_value = 0
                result = await stop_all_runs(db, requeue=True, flow_path=rel_path)
        assert result["ok"] is True

        with patch(
            "article_factory.services.flow_switch.stop_all_runs",
            AsyncMock(return_value={"stopped": 1, "run_ids": ["run-switch-last"]}),
        ):
            switched = await switch_active_flow(
                db,
                flow_path=rel_path,
                clear_history=False,
                update_queued=True,
                requeue_running=True,
            )
        assert "stopping" in switched["message"]
    finally:
        db.close()


def test_flow_storage_move_same_location(tmp_path, monkeypatch) -> None:
    from article_factory.services.flow_storage import flows_root, move_flow, write_flow
    from article_factory.services.flow_schema import new_flow_definition

    monkeypatch.setattr(settings, "flows_root", str(tmp_path / "flows"))
    flows_root()
    flow = new_flow_definition(slug="same-loc", display_name="Same", step_count=1)
    write_flow("same-loc.flow.json", flow)

    with pytest.raises(FileExistsError):
        move_flow("same-loc.flow.json", folder="", slug="same-loc")


def test_admin_invalid_api_key_and_missing_article_workspace(client, api_headers, configured_db) -> None:
    from article_factory.services.runtime_settings import set_factory_api_key

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "real-secret-key")
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/settings", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "test-factory-key")
        db.commit()
    finally:
        db.close()

    from article_factory.services.factory_api_key_cache import invalidate_factory_api_key_cache

    invalidate_factory_api_key_cache()

    missing_article = client.get(
        "/api/articles/missing-article/workspace-files/notes.md",
        headers=api_headers,
    )
    assert missing_article.status_code == 404


@pytest.mark.asyncio
async def test_factory_loop_stale_reservation_warning(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("held-puller")

    monkeypatch.setattr(
        "article_factory.orchestrator.runner.idle_pullers_for_model",
        lambda *_args, **_kwargs: [],
    )
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(return_value=[])
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", AsyncMock())

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "m1"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Stale Q", status="queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()


def test_queue_retry_rerunnable_edge_cases() -> None:
    from article_factory.services.queue_retry import is_queue_item_rerunnable

    item = TopicQueueItem(topic_slug="sports", prompt="X", status="paused")
    run = FactoryRun(run_id="run-done", topic_slug="sports", status="completed")
    assert is_queue_item_rerunnable(item, run) is True

    assert is_queue_item_rerunnable(item, None) is False


def test_personas_validation_errors(configured_db) -> None:
    from article_factory.models import Persona
    from article_factory.services.personas import _unique_slug, create_persona, update_persona

    db = db_module.SessionLocal()
    try:
        with pytest.raises(ValueError, match="name"):
            create_persona(db, {"name": "   ", "style_prompt": "style"})
        persona = create_persona(db, {"name": "Valid", "style_prompt": "style"})
        db.flush()
        row = db.query(Persona).filter_by(slug=persona["slug"]).one()
        assert _unique_slug(db, persona["slug"], exclude_id=row.id) == persona["slug"]
        db.commit()
        with pytest.raises(ValueError, match="name"):
            update_persona(db, persona["slug"], {"name": "   "})
    finally:
        db.close()


def test_run_recovery_commit_retry_and_reconstruct(configured_db, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from article_factory.services.run_recovery import (
        commit_with_retry,
        ensure_run_pipeline_state,
        reconstruct_pipeline_state,
    )

    locked = OperationalError("stmt", {}, Exception("database is locked"))
    attempts = {"n": 0}
    db = db_module.SessionLocal()

    def commit():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise locked
        return None

    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr("article_factory.services.run_recovery.time.sleep", lambda *_args: None)
    commit_with_retry(db)

    run = FactoryRun(
        run_id="run-recon-last",
        topic_slug="sports",
        flow_path="sports/standard-4-step.flow.json",
        status="running",
        current_step="missing-step",
    )
    db.add(run)
    db.commit()
    assert reconstruct_pipeline_state(db, run) is None

    run.current_step = "writer"
    db.commit()
    assert reconstruct_pipeline_state(db, run) is None

    run.pipeline_state = None
    assert ensure_run_pipeline_state(db, run) is False
    db.close()


def test_flow_storage_list_templates_empty(tmp_path, monkeypatch) -> None:
    from article_factory.services.flow_storage import flows_root, list_templates

    monkeypatch.setattr(settings, "flows_root", str(tmp_path / "flows-empty"))
    flows_root()
    assert list_templates() == []


def test_admin_resolve_flow_path_fallback(configured_db) -> None:
    from article_factory.routes.admin import _resolve_queue_flow_path

    db = db_module.SessionLocal()
    try:
        assert _resolve_queue_flow_path(db, "custom.flow.json", 99999) == "custom.flow.json"
    finally:
        db.close()


def test_run_recovery_existing_pipeline_state(configured_db) -> None:
    from article_factory.services.run_recovery import ensure_run_pipeline_state

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-has-state",
            topic_slug="sports",
            status="running",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()
        assert ensure_run_pipeline_state(db, run) is True
    finally:
        db.close()


def test_token_usage_tool_fallback_and_total_adjust() -> None:
    from article_factory.services.token_usage import enrich_step_record, normalize_round_usage

    usage = normalize_round_usage(
        {"input_tokens": 3, "output_tokens": 4, "total_tokens": 2},
        messages=[],
        assistant_message={},
    )
    assert usage["total_tokens"] == 7

    step = enrich_step_record(
        {
            "step_key": "",
            "prompt": "",
            "content": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "tools_used": [{"tool": "web_fetch", "detail": "https://example.com/page"}],
        }
    )
    assert int(step["usage"]["total_tokens"]) > 0


@pytest.mark.asyncio
async def test_execute_step_zero_rounds_raises(configured_db, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.workers.executor.MAX_TOOL_ROUNDS", 0)
    cp = AsyncMock(spec=ControlPlaneClient)
    with pytest.raises(RuntimeError, match="ended without"):
        await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p1",
            model="m1",
        )


def test_misc_service_branches(configured_db, monkeypatch) -> None:
    from article_factory.services.factory_identity import _hostname_fallback_id, load_factory_identity, save_factory_display_name
    from article_factory.services.factory_stats import _median, _prompt_for_run, build_factory_stats
    from article_factory.services.iteration_stats import attach_iteration_metadata
    from article_factory.services.run_attachments import _is_text_file

    monkeypatch.setattr("socket.gethostname", lambda: "")
    assert _hostname_fallback_id() == "factory-local"

    db = db_module.SessionLocal()
    try:
        row = load_factory_identity(db)
        save_factory_display_name(db, row.gateway_display_name or "Factory")
        stats = build_factory_stats(db)
        assert stats["summary"]["count"] >= 0
        assert _median([]) == 0
        run = FactoryRun(run_id="run-prompt", topic_slug="my-topic", status="completed")
        assert _prompt_for_run(run, None) == "My Topic"
        meta = attach_iteration_metadata({"draft_number": 1, "review_round": 0}, draft_number=1, review_round=0)
        assert meta["draft_number"] == 1
    finally:
        db.close()

    assert _is_text_file(b"\x00binary") is False
    assert _is_text_file(b"text") is True


def test_cms_error_message_non_string_detail() -> None:
    from article_factory.cms_client import cms_error_message

    response = MagicMock()
    response.status_code = 400
    response.request.method = "POST"
    response.request.url.path = "/runs"
    response.json.return_value = {"detail": {"code": "bad"}}

    message = cms_error_message(response)
    assert "Showroom CMS" in message


def test_flow_performance_gate_config_edges() -> None:
    from article_factory.services.flow_performance import resolve_gate_config
    from article_factory.services.flow_schema import FlowDefinition, FlowStep, new_flow_definition

    empty = FlowDefinition.model_construct(
        slug="empty",
        display_name="Empty",
        steps=[],
        performance=None,
    )
    assert resolve_gate_config(empty) == (None, [])

    flow = new_flow_definition(slug="loop", display_name="Loop", step_count=2)
    steps = sorted(flow.steps, key=lambda s: s.order)
    last = steps[-1]
    last.completion.can_loop = True
    last.completion.loop_goto_step_id = None
    gate, producers = resolve_gate_config(flow)
    assert gate == last.step_key
    assert producers == [steps[0].step_key]


@pytest.mark.asyncio
async def test_run_control_reassert_and_cancelled(configured_db) -> None:
    from article_factory.services.run_control import ensure_run_active, reassert_runs_stopped, request_run_cancel

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="run-reassert", topic_slug="sports", status="running")
        db.add(run)
        db.commit()
        updated = reassert_runs_stopped(db, ["run-reassert"])
        db.commit()
        assert updated == 1
        db.refresh(run)
        assert run.status == "cancelled"

        run2 = FactoryRun(run_id="run-ensure-cancel", topic_slug="sports", status="running")
        db.add(run2)
        db.commit()
        await request_run_cancel(run2.run_id)
        with pytest.raises(Exception):
            await ensure_run_active(db, run2)
    finally:
        db.close()


def test_run_attachments_invalid_utf8() -> None:
    from article_factory.services.run_attachments import _is_text_file

    assert _is_text_file(b"\xff\xfe") is False


def test_token_usage_enrich_tool_fallback_with_patch() -> None:
    from article_factory.services.token_usage import enrich_step_record

    with patch("article_factory.services.token_usage.normalize_usage") as norm:
        norm.side_effect = [
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            {"input_tokens": 4, "output_tokens": 0, "total_tokens": 4},
        ]
        step = enrich_step_record(
            {
                "step_key": "writer",
                "content": "",
                "tools_used": [{"tool": "web_fetch", "detail": "https://example.com"}],
            }
        )
    assert step["usage"]["total_tokens"] == 4


@pytest.mark.asyncio
async def test_showroom_refresh_locked_final_attempt(configured_db, monkeypatch) -> None:
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

    with patch(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        AsyncMock(side_effect=locked),
    ):
        assert await refresh_showroom_status(max_attempts=1) is False


def test_admin_article_workspace_value_error(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            CompletedArticle(
                run_id="run-ws-val",
                topic_slug="sports",
                title="T",
                summary="S",
                body_markdown="# T",
                manifest={},
            )
        )
        db.commit()
    finally:
        db.close()

    bad = client.get(
        "/api/articles/run-ws-val/workspace-files/..%2Fsecret.txt",
        headers=api_headers,
    )
    assert bad.status_code == 400


def test_app_startup_readiness_exception(configured_db, monkeypatch) -> None:
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
        AsyncMock(side_effect=RuntimeError("readiness boom")),
    )

    with TestClient(create_app()) as test_client:
        assert test_client.get("/api/health").status_code == 200
