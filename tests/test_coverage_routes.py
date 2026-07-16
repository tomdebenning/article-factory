from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import article_factory.db as db_module
from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem
from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_storage import create_flow, write_flow


def test_flows_tree_and_list(client, api_headers) -> None:
    tree = client.get("/api/flows/tree", headers=api_headers)
    assert tree.status_code == 200

    listing = client.get("/api/flows/list", headers=api_headers, params={"path": "sports"})
    assert listing.status_code == 200


def test_flows_create_update_duplicate_export(client, api_headers, configured_db) -> None:
    created = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "api-cov", "slug": "cov-flow", "display_name": "Cov", "step_count": 2},
    )
    assert created.status_code == 200
    path = created.json()["path"]

    read = client.get("/api/flows/file", headers=api_headers, params={"path": path})
    assert read.status_code == 200

    updated = client.put(
        "/api/flows/file",
        headers=api_headers,
        params={"path": path},
        json={"flow": read.json()["flow"]},
    )
    assert updated.status_code == 200

    exported = client.get("/api/flows/export", headers=api_headers, params={"path": path})
    assert exported.status_code == 200

    dup = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": path, "folder": "api-cov", "slug": "cov-copy"},
    )
    assert dup.status_code == 200


def test_flows_from_template(client, api_headers) -> None:
    templates = client.get("/api/flows/templates", headers=api_headers)
    assert templates.status_code == 200
    items = templates.json().get("templates") or []
    if not items:
        pytest.skip("no templates")
    template_path = items[0]["path"]
    created = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": template_path,
            "folder": "api-template",
            "slug": "from-tpl",
            "display_name": "From Template",
        },
    )
    assert created.status_code == 200


def test_flow_queues_crud_and_presets(client, api_headers, configured_db) -> None:
    rel_path, _ = create_flow(folder="", slug="queue-flow", display_name="Queue Flow", step_count=1)

    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "Test Queue", "flow_path": rel_path, "topic_slug": "sports"},
    )
    assert created.status_code == 200
    queue_id = created.json()["queue"]["id"]

    listing = client.get("/api/flow-queues", headers=api_headers)
    assert listing.status_code == 200

    updated = client.put(
        f"/api/flow-queues/{queue_id}",
        headers=api_headers,
        json={"name": "Renamed Queue"},
    )
    assert updated.status_code == 200

    presets = client.get("/api/flow-queues/presets", headers=api_headers)
    assert presets.status_code == 200

    missing_preset = client.get("/api/flow-queues/presets/missing-preset-slug", headers=api_headers)
    assert missing_preset.status_code == 404

    bad_start = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={"topics": [], "default_model": "", "flow_path": ""},
    )
    assert bad_start.status_code in {400, 422}

    deleted = client.delete(f"/api/flow-queues/{queue_id}", headers=api_headers)
    assert deleted.status_code == 200


def test_publish_run_failure(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-pub-fail",
                topic_slug="sports",
                status="completed",
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-pub-fail",
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
        AsyncMock(side_effect=RuntimeError("cms down")),
    )
    response = client.post("/api/runs/run-pub-fail/publish", headers=api_headers)
    assert response.status_code == 502


def test_publish_run_recovers_failed_status(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-pub-recover",
                topic_slug="sports",
                status="failed",
                error="old",
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-pub-recover",
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
    response = client.post("/api/runs/run-pub-recover/publish", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["run"]["status"] == "completed"


def test_factory_stop_all_runs(client, api_headers, monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.routes.admin.stop_all_runs",
        AsyncMock(return_value={"stopped": 0}),
    )
    response = client.post(
        "/api/factory/stop-all-runs",
        headers=api_headers,
        json={"requeue": False},
    )
    assert response.status_code == 200


def test_switch_flow_endpoint(client, api_headers, monkeypatch) -> None:
    monkeypatch.setattr(
        "article_factory.routes.admin.switch_active_flow",
        AsyncMock(return_value={"ok": True, "flow_path": "sports/standard-4-step.flow.json"}),
    )
    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={"flow_path": "sports/standard-4-step.flow.json"},
    )
    assert response.status_code == 200


def test_enqueue_batch(client, api_headers) -> None:
    response = client.post(
        "/api/queue/batch",
        headers=api_headers,
        json={
            "topic_slug": "sports",
            "topics": ["Topic A", "Topic B"],
            "priority": 10,
        },
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2


def test_flow_performance_route_filters(client, api_headers, configured_db) -> None:
    rel_path, _ = create_flow(folder="", slug="perf-route", display_name="Perf Route", step_count=1)
    perf = client.get(
        "/api/flows/performance",
        headers=api_headers,
        params={"path": rel_path, "selected_model": "none-model"},
    )
    assert perf.status_code == 200


def test_run_recovery_fail_interrupted(configured_db) -> None:
    from article_factory.services.run_recovery import fail_interrupted_run

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="T", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-interrupt",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
        )
        db.add(run)
        db.commit()
        fail_interrupted_run(db, run, message="Interrupted")
        db.refresh(run)
        db.refresh(item)
        assert run.status == "failed"
        assert item.status == "failed"
    finally:
        db.close()


def test_flow_storage_template_validation(configured_db) -> None:
    from article_factory.services.flow_storage import create_flow_from_template

    with pytest.raises(ValueError, match="_templates"):
        create_flow_from_template(
            template_path="sports/standard-4-step.flow.json",
            folder="x",
            slug="bad",
            display_name="Bad",
        )
