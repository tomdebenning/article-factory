from __future__ import annotations

import pytest

import article_factory.db as db_module
from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.services.runtime_settings import update_factory_settings


@pytest.fixture
def failed_queue_item(configured_db) -> int:
    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
                "default_model": "llama3",
                "brave_search_api_key": "test-brave-key",
            },
        )
        item = TopicQueueItem(
            topic_slug="sports",
            prompt="Failed topic",
            status="failed",
            priority=100,
        )
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-failed",
                topic_slug="sports",
                queue_item_id=item.id,
                status="failed",
                error="Showroom CMS unreachable: connection refused",
            )
        )
        db.commit()
        return item.id
    finally:
        db.close()


def test_list_queue_includes_run_error(client, api_headers, failed_queue_item) -> None:
    response = client.get("/api/queue", headers=api_headers)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["id"] == failed_queue_item)
    assert item["run_error"] == "Showroom CMS unreachable: connection refused"


@pytest.mark.asyncio
async def test_retry_status_for_failed_item(client, api_headers, failed_queue_item, monkeypatch) -> None:
    async def cms_ok(*args, **kwargs):
        return True, "Connected"

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr("article_factory.services.factory_readiness.check_cms_connection", cms_ok)
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    response = client.get(f"/api/queue/{failed_queue_item}/retry-status", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["retriable"] is True
    assert body["can_retry"] is True


def test_retry_failed_item_success(client, api_headers, failed_queue_item, monkeypatch) -> None:
    async def cms_ok(*args, **kwargs):
        return True, "Connected"

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr("article_factory.services.factory_readiness.check_cms_connection", cms_ok)
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    response = client.post(f"/api/queue/{failed_queue_item}/retry", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["status"] == "queued"

    listing = client.get("/api/queue", headers=api_headers)
    item = next(i for i in listing.json()["items"] if i["id"] == failed_queue_item)
    assert item["status"] == "queued"


def test_retry_failed_item_blocked_without_model(client, api_headers, failed_queue_item) -> None:
    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"default_model": ""})
    finally:
        db.close()

    response = client.post(f"/api/queue/{failed_queue_item}/retry", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["blockers"]
    assert any(b["id"] == "model" for b in body["blockers"])

    db = db_module.SessionLocal()
    try:
        item = db.get(TopicQueueItem, failed_queue_item)
        assert item.status == "failed"
    finally:
        db.close()


def test_retry_non_failed_item_rejected(client, api_headers, configured_db) -> None:
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
    assert response.json()["ok"] is False


def test_retry_completed_item_success(client, api_headers, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
                "default_model": "llama3",
                "brave_search_api_key": "test-brave-key",
            },
        )
        item = TopicQueueItem(
            topic_slug="sports",
            prompt="Done topic",
            status="completed",
            priority=100,
        )
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-done",
                topic_slug="sports",
                queue_item_id=item.id,
                status="completed",
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()

    async def cms_ok(*args, **kwargs):
        return True, "Connected"

    class FakeCp:
        async def list_pullers(self, *, active_only=False):
            return [
                {
                    "puller_name": "gpu-01",
                    "is_active": True,
                    "is_stale": False,
                    "status": "ok",
                    "supported_models": ["llama3"],
                }
            ]

    monkeypatch.setattr("article_factory.services.factory_readiness.check_cms_connection", cms_ok)
    monkeypatch.setattr(
        "article_factory.services.factory_readiness.ControlPlaneClient",
        lambda base_url: FakeCp(),
    )

    import httpx

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            class Resp:
                def raise_for_status(self): ...

            return Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())

    response = client.post(f"/api/queue/{item_id}/retry", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["status"] == "queued"
