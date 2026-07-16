from __future__ import annotations

from unittest.mock import AsyncMock

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


def test_enqueue_batch(client, api_headers) -> None:
    response = client.post(
        "/api/queue/batch",
        headers=api_headers,
        json={
            "topic_slug": "sports",
            "topics": ["First topic", "", "Second topic"],
            "priority": 10,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2


def test_get_run_not_found(client, api_headers) -> None:
    response = client.get("/api/runs/missing-run", headers=api_headers)
    assert response.status_code == 404


def test_get_run_with_flow_version(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun
    from article_factory.services.flow_versions import ensure_flow_version_for_run

    db = db_module.SessionLocal()
    try:
        version = ensure_flow_version_for_run(db, "sports/standard-4-step.flow.json")
        version_number = version.version_number
        db.add(
            FactoryRun(
                run_id="run-versioned",
                topic_slug="sports",
                status="completed",
                flow_version_id=version.id,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/runs/run-versioned", headers=api_headers)
    assert response.status_code == 200
    run = response.json()["run"]
    assert run["flow_version_number"] == version_number


def test_delete_run_success(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun, TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Done", status="completed")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-delete",
                topic_slug="sports",
                queue_item_id=item.id,
                status="completed",
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    response = client.delete("/api/runs/run-delete", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["deleted_run_id"] == "run-delete"

    db = db_module.SessionLocal()
    try:
        item = db.get(TopicQueueItem, item_id)
        assert item.status == "queued"
    finally:
        db.close()


def test_delete_run_not_found(client, api_headers) -> None:
    response = client.delete("/api/runs/missing", headers=api_headers)
    assert response.status_code == 404


def test_delete_run_rejects_active(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-active", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    response = client.delete("/api/runs/run-active", headers=api_headers)
    assert response.status_code == 409


def test_stop_run_not_found(client, api_headers) -> None:
    response = client.post("/api/runs/missing/stop", headers=api_headers)
    assert response.status_code == 404


def test_stop_run_not_active(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-done", topic_slug="sports", status="completed"))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/runs/run-done/stop", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not active" in body["message"]


def test_stop_run_success(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun, TopicQueueItem
    from article_factory.orchestrator.runner import factory_loop

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Stop me", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-stop-admin",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(factory_loop, "cancel_run_workers", lambda **kwargs: 1)
    dispatched = {"called": False}
    monkeypatch.setattr(factory_loop, "request_dispatch", lambda: dispatched.__setitem__("called", True))

    response = client.post("/api/runs/run-stop-admin/stop", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["run"]["status"] == "cancelled"
    assert dispatched["called"] is True


def test_stop_run_already_requested(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-cancel-pending", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    async def already_cancelled(_run_id: str) -> bool:
        return True

    monkeypatch.setattr("article_factory.routes.admin.is_run_cancelled", already_cancelled)

    response = client.post("/api/runs/run-cancel-pending/stop", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "already requested" in body["message"]


def test_queue_retry_status_not_found(client, api_headers) -> None:
    response = client.get("/api/queue/99999/retry-status", headers=api_headers)
    assert response.status_code == 404


def test_retry_queue_item_not_rerunnable(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Queued", status="queued")
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()

    response = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "cannot be re-run" in body["message"]


def test_retry_queue_item_success(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun, TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Failed", status="failed")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-retry",
                topic_slug="sports",
                queue_item_id=item.id,
                status="failed",
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    async def ready_assessment(db):
        return {
            "can_retry": True,
            "message": "Ready",
            "blockers": [],
        }

    monkeypatch.setattr("article_factory.routes.admin._retry_assessment", ready_assessment)

    from article_factory.orchestrator.runner import factory_loop

    dispatched = {"called": False}
    monkeypatch.setattr(factory_loop, "request_dispatch", lambda: dispatched.__setitem__("called", True))

    response = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["status"] == "queued"
    assert dispatched["called"] is True


def test_put_gateway_identity_validation(client, api_headers) -> None:
    response = client.put(
        "/api/settings/gateway-identity",
        headers=api_headers,
        json={"gateway_display_name": "   "},
    )
    assert response.status_code == 400


def test_test_control_plane_with_body(client, api_headers, monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            return FakeResponse()

    monkeypatch.setattr("article_factory.routes.admin.httpx.AsyncClient", lambda **kwargs: FakeClient())

    response = client.post(
        "/api/settings/test/control-plane",
        headers=api_headers,
        json={"control_plane_url": "http://cp.test:8000"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_control_plane_failure(client, api_headers, monkeypatch) -> None:
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            raise RuntimeError("unreachable")

    monkeypatch.setattr("article_factory.routes.admin.httpx.AsyncClient", lambda **kwargs: FakeClient())

    response = client.post("/api/settings/test/control-plane", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_test_brave_search_missing_key(client, api_headers, configured_db) -> None:
    response = client.post("/api/settings/test/brave-search", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "API key" in body["message"]


def test_list_control_plane_pullers_error(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
    finally:
        db.close()

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(
        "article_factory.routes.admin.ControlPlaneClient",
        lambda **kwargs: mock_cp,
    )

    response = client.get("/api/control-plane/pullers", headers=api_headers)
    assert response.status_code == 502


def test_list_articles_and_get_article(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import CompletedArticle, FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-article", topic_slug="sports", status="completed"))
        db.add(
            CompletedArticle(
                run_id="run-article",
                topic_slug="sports",
                title="Title",
                summary="Summary",
                body_markdown="# Title\n\nBody",
            )
        )
        db.commit()
    finally:
        db.close()

    listing = client.get("/api/articles", headers=api_headers)
    assert listing.status_code == 200
    assert any(a["run_id"] == "run-article" for a in listing.json()["articles"])

    detail = client.get("/api/runs/run-article", headers=api_headers)
    assert detail.status_code == 200

    article = client.get("/api/articles/run-article", headers=api_headers)
    assert article.status_code == 200
    assert article.json()["article"]["title"] == "Title"


def test_publish_run_failure(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import CompletedArticle, FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-fail-pub", topic_slug="sports", status="completed"))
        db.add(
            CompletedArticle(
                run_id="run-fail-pub",
                topic_slug="sports",
                title="Title",
                summary="Summary",
                body_markdown="# Title\n\nBody",
            )
        )
        db.commit()
    finally:
        db.close()

    async def boom_publish(db, *, run, article, cms=None, runtime=None):
        raise RuntimeError("publish failed")

    monkeypatch.setattr("article_factory.routes.admin.publish_article_to_showroom", boom_publish)

    response = client.post("/api/runs/run-fail-pub/publish", headers=api_headers)
    assert response.status_code == 502
    assert "publish failed" in response.json()["detail"]


def test_get_article_not_found(client, api_headers) -> None:
    response = client.get("/api/articles/missing", headers=api_headers)
    assert response.status_code == 404


def test_factory_stats(client, api_headers) -> None:
    response = client.get("/api/stats", headers=api_headers)
    assert response.status_code == 200
    assert "summary" in response.json()


def test_queue_retry_status(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun, TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Failed", status="failed")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-retry-status",
                topic_slug="sports",
                queue_item_id=item.id,
                status="failed",
                error="boom",
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    async def ready_assessment(db):
        return {"can_retry": True, "message": "Ready", "blockers": []}

    monkeypatch.setattr("article_factory.routes.admin._retry_assessment", ready_assessment)

    response = client.get(f"/api/queue/{item_id}/retry-status", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["can_retry"] is True
    assert body["retriable"] is True
    assert body["run_error"] == "boom"


def test_retry_queue_item_blocked_by_readiness(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun, TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Failed", status="failed")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-blocked",
                topic_slug="sports",
                queue_item_id=item.id,
                status="failed",
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    async def blocked_assessment(db):
        return {
            "can_retry": False,
            "message": "Not ready",
            "blockers": [
                {
                    "id": "model",
                    "label": "Model",
                    "message": "No model configured",
                }
            ],
        }

    monkeypatch.setattr("article_factory.routes.admin._retry_assessment", blocked_assessment)

    response = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["blockers"]


def test_control_plane_task_status_not_found(client, api_headers, configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from unittest.mock import AsyncMock
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
    finally:
        db.close()

    mock_cp = AsyncMock()
    mock_cp.get_task_status = AsyncMock(return_value=None)
    monkeypatch.setattr("article_factory.routes.admin.ControlPlaneClient", lambda **kwargs: mock_cp)

    response = client.get(
        "/api/control-plane/tasks/status",
        headers=api_headers,
        params={"conversation_id": "conv-1"},
    )
    assert response.status_code == 200
    assert response.json()["found"] is False


def test_publish_run_no_article(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-no-article", topic_slug="sports", status="completed"))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/runs/run-no-article/publish", headers=api_headers)
    assert response.status_code == 404
