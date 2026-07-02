from __future__ import annotations

from article_factory.config import settings


def test_health(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_requires_api_key(client, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.services.runtime_settings import set_factory_api_key

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "required-secret")
    finally:
        db.close()

    response = client.get("/api/flows/tree")
    assert response.status_code == 401


def test_queue_and_runs(client, api_headers) -> None:
    enqueue = client.post(
        "/api/queue",
        headers=api_headers,
        json={"topic_slug": "sports", "prompt": "Cover the game", "priority": 50},
    )
    assert enqueue.status_code == 200
    assert enqueue.json()["status"] == "queued"

    listing = client.get("/api/queue", headers=api_headers)
    assert listing.status_code == 200
    assert len(listing.json()["items"]) >= 1

    runs = client.get("/api/runs", headers=api_headers)
    assert runs.status_code == 200
    assert runs.json()["runs"] == []


def test_queue_with_run_steps(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun, StepExecution, TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Queued", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-queue",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
            )
        )
        db.add(
            StepExecution(
                run_id="run-queue",
                step_key="writer",
                status="waiting",
                puller="gpu-01",
                model="llama3",
            )
        )
        db.commit()
    finally:
        db.close()

    listing = client.get("/api/queue", headers=api_headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    match = next(i for i in items if i.get("run_id") == "run-queue")
    assert match["steps"][0]["status"] == "waiting"


def test_list_runs_includes_steps(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun, StepExecution

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-list", topic_slug="sports", status="completed"))
        db.add(
            StepExecution(
                run_id="run-list",
                step_key="writer",
                status="completed",
                puller="gpu-01",
                model="llama3",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/runs", headers=api_headers)
    assert response.status_code == 200
    run = next(r for r in response.json()["runs"] if r["run_id"] == "run-list")
    assert run["steps"][0]["step_key"] == "writer"


def test_factory_status(client, api_headers) -> None:
    response = client.get("/api/factory/status", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert body["queue_depth"] >= 0


def test_settings_include_default_flow_path(client, api_headers) -> None:
    response = client.get("/api/settings", headers=api_headers)
    assert response.status_code == 200
    assert "default_flow_path" in response.json()

    updated = client.put(
        "/api/settings",
        headers=api_headers,
        json={
            "control_plane_url": "http://cp.local:8000",
            "cms_url": "http://cms.local:8200",
            "cms_api_key": "cms-key",
            "default_puller": "gpu-01",
            "default_model": "llama3",
            "default_flow_path": "test/SimpleTest.flow.json",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["default_flow_path"] == "test/SimpleTest.flow.json"


def test_publish_run_to_showroom(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module

    from article_factory.models import CompletedArticle, FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-publish",
                topic_slug="general",
                status="failed",
                error="Showroom CMS: Unknown topic general",
                manifest={"stats": {"total_tokens": 10}},
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-publish",
                topic_slug="general",
                title="Cats Article",
                summary="About cats",
                body_markdown="# Cats\n\nMeow",
                manifest={"stats": {"total_tokens": 10}},
            )
        )
        db.commit()
    finally:
        db.close()

    async def fake_publish(db, *, run, article, cms=None, runtime=None):
        return {"article_id": 42}

    monkeypatch.setattr(
        "article_factory.routes.admin.publish_article_to_showroom",
        fake_publish,
    )

    response = client.post("/api/runs/run-publish/publish", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["run"]["status"] == "completed"
    assert body["run"]["error"] is None
