from __future__ import annotations

from unittest.mock import AsyncMock, patch

import article_factory.db as db_module
from article_factory.config import settings
from article_factory.models import CompletedArticle, FactoryRun, StepExecution, TopicQueueItem
from article_factory.services.flow_storage import save_step_response_to_disk


def test_stop_running_run(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Running topic", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-stop-api",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "article_factory.routes.admin.factory_loop.cancel_run_workers",
        lambda **kwargs: 1,
    )
    monkeypatch.setattr(
        "article_factory.routes.admin.is_run_cancelled",
        AsyncMock(return_value=False),
    )

    response = client.post("/api/runs/run-stop-api/stop", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["run"]["status"] == "cancelled"


def test_stop_run_already_cancelled(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-already-stop",
                topic_slug="sports",
                status="running",
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "article_factory.routes.admin.is_run_cancelled",
        AsyncMock(return_value=True),
    )

    response = client.post("/api/runs/run-already-stop/stop", headers=api_headers)
    assert response.json()["ok"] is False
    assert "already requested" in response.json()["message"]


def test_article_and_run_step_files(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-files",
                topic_slug="sports",
                status="completed",
                manifest={"steps": []},
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-files",
                topic_slug="sports",
                title="Title",
                summary="Summary",
                body_markdown="# Title\n\nBody",
                manifest={"steps": []},
            )
        )
        db.commit()
    finally:
        db.close()

    save_step_response_to_disk(
        run_id="run-files",
        step_order=1,
        step_key="writer",
        content="# Draft\n\nText",
    )

    run_file = client.get("/api/runs/run-files/step-files/01-writer.md", headers=api_headers)
    assert run_file.status_code == 200
    assert "Draft" in run_file.json()["content"]

    article_file = client.get("/api/articles/run-files/step-files/01-writer.md", headers=api_headers)
    assert article_file.status_code == 200

    missing_article = client.get("/api/articles/missing/step-files/01-writer.md", headers=api_headers)
    assert missing_article.status_code == 404

    bad_name = client.get("/api/runs/run-files/step-files/not-a-step.txt", headers=api_headers)
    assert bad_name.status_code == 400


def test_article_workspace_file(client, api_headers, configured_db, tmp_path, monkeypatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    workspace = tmp_path / "run-ws" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "notes.txt").write_text("workspace notes", encoding="utf-8")

    db = db_module.SessionLocal()
    try:
        db.add(
            CompletedArticle(
                run_id="run-ws",
                topic_slug="sports",
                title="T",
                summary="S",
                body_markdown="# T\n\nB",
                manifest={},
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/articles/run-ws/workspace-files/notes.txt", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["content"] == "workspace notes"


def test_retry_with_active_run_message(client, api_headers, configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
                "default_model": "llama3",
                "brave_search_api_key": "key",
            },
        )
        item = TopicQueueItem(topic_slug="sports", prompt="Retry me", status="failed", priority=50)
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-failed-retry",
                topic_slug="sports",
                queue_item_id=item.id,
                status="failed",
            )
        )
        db.add(
            FactoryRun(
                run_id="run-active-block",
                topic_slug="sports",
                status="running",
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
    assert "after the current article finishes" in body["message"]


def test_get_run_with_version_and_queue_label(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version
    from article_factory.services.flow_queues import ensure_default_flow_queue
    from article_factory.services.topic_queue_snapshots import get_or_create_topic_queue_snapshot

    db = db_module.SessionLocal()
    try:
        rel_path, _flow = create_flow(folder="", slug="detail-flow", display_name="Detail", step_count=2)
        version = create_flow_version(db, rel_path, message="v1")
        queue = ensure_default_flow_queue(db)
        item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug="sports",
            prompt="Topic",
            status="queued",
        )
        db.add(item)
        db.flush()
        snapshot = get_or_create_topic_queue_snapshot(db, flow_queue_id=queue.id)
        assert snapshot is not None
        db.add(
            FactoryRun(
                run_id="run-rich",
                topic_slug="sports",
                status="completed",
                flow_path=rel_path,
                flow_version_id=version.id,
                topic_queue_snapshot_id=snapshot.id,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/runs/run-rich", headers=api_headers)
    assert response.status_code == 200
    run = response.json()["run"]
    assert run["flow_version_number"] == 1
    assert run["topic_queue_label"] is not None
