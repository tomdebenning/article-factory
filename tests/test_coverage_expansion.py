from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import article_factory.db as db_module
from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem
from article_factory.orchestrator.pipeline import serialize_active_run
from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_steps import (
    flow_path_for_run,
    flow_steps_payload,
    flow_steps_payload_for_run,
    step_display_name,
)
from article_factory.services.flow_storage import create_flow, write_flow
from article_factory.services.flow_versions import create_flow_version
from article_factory.services.topic_queue_snapshots import get_or_create_topic_queue_snapshot


def test_flow_steps_payload_empty_and_missing(configured_db) -> None:
    assert flow_steps_payload("") == []
    assert flow_steps_payload("missing/flow.flow.json") == []


def test_flow_steps_payload_for_run_uses_version(configured_db) -> None:
    rel_path, flow = create_flow(folder="", slug="steps-version", display_name="Steps", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, flow=flow, message="v1")
        run = FactoryRun(
            run_id="run-steps-version",
            topic_slug="general",
            flow_path=rel_path,
            flow_version_id=version.id,
            status="running",
        )
        steps = flow_steps_payload_for_run(db, run)
        assert len(steps) == 2
    finally:
        db.close()


def test_flow_path_for_run_and_step_display_name(configured_db) -> None:
    write_flow("test/display.flow.json", new_flow_definition(slug="display", display_name="Display", step_count=1))
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="r", topic_slug="general", flow_path="test/display.flow.json", status="running")
        assert flow_path_for_run(db, run) == "test/display.flow.json"
        assert step_display_name("test/display.flow.json", "step_1") == "Step 1"
        assert step_display_name(None, "writer") == "Writer"
        assert flow_path_for_run(db, None).endswith(".flow.json")
    finally:
        db.close()


def test_serialize_active_run_with_queue_prompt(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="general", prompt="My topic prompt", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-serialize",
            topic_slug="general",
            queue_item_id=item.id,
            flow_path="sports/standard-4-step.flow.json",
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()
        payload = serialize_active_run(db, run)
        assert payload["topic_prompt"] == "My topic prompt"
        assert payload["flow_steps"]
        assert isinstance(payload["steps"], list)
    finally:
        db.close()


def test_serialize_active_run_fallback_step(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-fallback-step",
            topic_slug="general",
            flow_path="missing/flow.flow.json",
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()
        with patch(
            "article_factory.services.step_trace.step_executions_payload",
            side_effect=RuntimeError("db error"),
        ):
            payload = serialize_active_run(db, run)
        assert payload["steps"] == [{"step_key": "writer", "status": "pulled", "progress": {}, "tools_used": []}]
    finally:
        db.close()


def test_flow_performance_routes(client, api_headers, configured_db) -> None:
    rel_path, _flow = create_flow(folder="", slug="route-perf", display_name="Route Perf", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="run-route-perf",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
                manifest={"steps": [{"step_key": "step_1"}, {"step_key": "step_2", "content": "VERDICT: ACCEPT"}]},
            )
        )
        db.commit()
        version_id = version.id
    finally:
        db.close()

    versions = client.get(f"/api/flows/versions?path={rel_path}", headers=api_headers)
    assert versions.status_code == 200
    assert versions.json()["versions"][0]["version_number"] == 1

    detail = client.get(f"/api/flows/versions/detail?version_id={version_id}", headers=api_headers)
    assert detail.status_code == 200
    assert detail.json()["version"]["flow_content"]

    missing_version = client.get("/api/flows/versions/detail?version_id=999999", headers=api_headers)
    assert missing_version.status_code == 404

    apply = client.post(
        "/api/flows/versions/apply",
        headers=api_headers,
        json={"version_id": version_id},
    )
    assert apply.status_code == 200

    topic_queues = client.get(f"/api/flows/topic-queues?path={rel_path}", headers=api_headers)
    assert topic_queues.status_code == 200

    error_groups = client.get("/api/flows/error-groups", headers=api_headers)
    assert error_groups.status_code == 200
    assert any(item["error_group"] == "completed" for item in error_groups.json()["error_groups"])

    tagged = client.put(
        "/api/flows/runs/run-route-perf/error-tag",
        headers=api_headers,
        json={"error_group": "completed", "note": "looks fine"},
    )
    assert tagged.status_code == 200
    assert tagged.json()["error_tag"]["error_group"] == "completed"


def test_flow_performance_create_version_missing(client, api_headers) -> None:
    response = client.post(
        "/api/flows/versions",
        headers=api_headers,
        json={"path": "missing/flow.flow.json", "message": "nope"},
    )
    assert response.status_code == 404


def test_telemetry_routes(client, api_headers, configured_db) -> None:
    rel_path, _flow = create_flow(folder="", slug="tel-route", display_name="Tel", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="run-tel-route",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
                manifest={"steps": [{"step_key": "step_1"}, {"step_key": "step_2", "content": "VERDICT: ACCEPT"}]},
            )
        )
        db.commit()
        from article_factory.services.telemetry import capture_run_telemetry

        capture_run_telemetry(db, "run-tel-route")
        version_id = version.id
    finally:
        db.close()

    summary = client.get(
        f"/api/flows/telemetry?path={rel_path}&flow_version_id={version_id}&status=completed",
        headers=api_headers,
    )
    assert summary.status_code == 200
    assert summary.json()["total"] >= 1


def test_cli_telemetry_rebuild_missing_run(configured_db, monkeypatch, capsys) -> None:
    import sys

    with patch.object(sys, "argv", ["article-factory", "telemetry", "rebuild", "--run-id", "missing-run-id"]):
        with pytest.raises(SystemExit) as exc:
            from article_factory.__main__ import main

            main()
        assert exc.value.code == 1
    assert "No telemetry captured" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_flow_runner_no_model_configured(configured_db, monkeypatch) -> None:
    from dataclasses import replace

    async def fake_select(_cp, model: str) -> str:
        return "puller-1"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)

    from article_factory.orchestrator.flow_runner import execute_flow_pipeline
    from article_factory.services.runtime_settings import load_runtime_settings

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-no-model",
            topic_slug="general",
            flow_path="sports/standard-4-step.flow.json",
            status="running",
            selected_model="",
        )
        db.add(run)
        db.commit()
        runtime = replace(load_runtime_settings(db), default_model="")
        with pytest.raises(RuntimeError, match="No model configured"):
            await execute_flow_pipeline(
                db,
                run=run,
                flow_path="sports/standard-4-step.flow.json",
                topic_prompt="Topic",
                runtime=runtime,
                cms=None,
                emit_step_started=AsyncMock(),
                complete_run=AsyncMock(),
            )
    finally:
        db.close()


def test_telemetry_loads_completed_article(configured_db) -> None:
    from article_factory.services.telemetry import capture_run_telemetry

    rel_path, _flow = create_flow(folder="", slug="tel-article", display_name="Tel", step_count=1)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="run-tel-article",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
                manifest={"steps": [{"step_key": "step_1", "content": "body"}]},
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-tel-article",
                topic_slug="general",
                title="Title",
                body_markdown="# Article body from DB",
            )
        )
        db.commit()
        row = capture_run_telemetry(db, "run-tel-article")
        assert row is not None
        assert "Article body from DB" in (row.final_article_text or "")
    finally:
        db.close()


def test_batch_comparison_route(client, api_headers, configured_db) -> None:
    from article_factory.models import FlowQueue
    from article_factory.services.flow_queues import create_flow_queue, enqueue_topics_to_queue

    rel_path = "sports/standard-4-step.flow.json"
    db = db_module.SessionLocal()
    try:
        queue = create_flow_queue(db, name="Batch", flow_path=rel_path, topic_slug="sports")
        enqueue_topics_to_queue(db, queue.id, ["Topic A"])
        db.commit()
        snapshot = get_or_create_topic_queue_snapshot(db, flow_queue_id=queue.id)
        db.commit()
        snapshot_id = snapshot.id
    finally:
        db.close()

    missing = client.get("/api/flows/batch-comparison?topic_queue_snapshot_id=99999", headers=api_headers)
    assert missing.status_code == 404

    if snapshot_id:
        ok = client.get(
            f"/api/flows/batch-comparison?topic_queue_snapshot_id={snapshot_id}",
            headers=api_headers,
        )
        assert ok.status_code == 200
