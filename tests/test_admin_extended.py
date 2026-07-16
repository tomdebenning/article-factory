from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import article_factory.db as db_module
from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem


def test_gateway_identity_validation(client, api_headers) -> None:
    bad = client.put(
        "/api/settings/gateway-identity",
        headers=api_headers,
        json={"gateway_display_name": "   "},
    )
    assert bad.status_code == 400


def test_control_plane_pullers_and_task_status(client, api_headers, monkeypatch) -> None:
    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(return_value=[{"puller_name": "gpu-01", "supported_models": ["m1"]}])
    mock_cp.get_task_status = AsyncMock(return_value={"found": True, "status": "fetched"})

    with patch("article_factory.routes.admin.ControlPlaneClient", return_value=mock_cp):
        pullers = client.get("/api/control-plane/pullers", headers=api_headers)
        assert pullers.status_code == 200
        assert pullers.json()["pullers"][0]["puller_name"] == "gpu-01"

        status = client.get(
            "/api/control-plane/tasks/status",
            headers=api_headers,
            params={"conversation_id": "conv-1"},
        )
        assert status.status_code == 200
        assert status.json()["found"] is True

        mock_cp.get_task_status = AsyncMock(return_value=None)
        missing = client.get(
            "/api/control-plane/tasks/status",
            headers=api_headers,
            params={"conversation_id": "missing"},
        )
        assert missing.json()["found"] is False


def test_connection_test_endpoints(client, api_headers, monkeypatch) -> None:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.routes.admin.httpx.AsyncClient", return_value=mock_http):
        cp = client.post(
            "/api/settings/test/control-plane",
            headers=api_headers,
            json={"control_plane_url": "http://cp.test:8000", "cms_url": "", "cms_api_key": ""},
        )
        assert cp.status_code == 200
        assert cp.json()["ok"] is True

    with patch("article_factory.routes.admin.check_cms_connection", AsyncMock(return_value=(True, "ok"))):
        cms = client.post(
            "/api/settings/test/cms",
            headers=api_headers,
            json={
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
            },
        )
        assert cms.status_code == 200
        assert cms.json()["ok"] is True

    no_key = client.post("/api/settings/test/brave-search", headers=api_headers)
    assert no_key.json()["ok"] is False

    with patch(
        "article_factory.routes.admin.brave_web_search",
        AsyncMock(return_value={"web": {"results": [{"title": "T", "url": "u", "description": "d"}]}, "query": {}}),
    ):
        with_key = client.post(
            "/api/settings/test/brave-search",
            headers=api_headers,
            json={
                "control_plane_url": "http://cp.test:8000",
                "brave_search_api_key": "test-key",
            },
        )
        assert with_key.status_code == 200
        assert with_key.json()["ok"] is True


def test_queue_retry_status_and_missing_item(client, api_headers) -> None:
    missing = client.get("/api/queue/99999/retry-status", headers=api_headers)
    assert missing.status_code == 404

    retry_missing = client.post("/api/queue/99999/retry", headers=api_headers)
    assert retry_missing.status_code == 404


def test_run_detail_stop_delete_and_articles(client, api_headers, configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="completed")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-detail",
                topic_slug="sports",
                queue_item_id=item.id,
                status="completed",
                flow_path="sports/standard-4-step.flow.json",
                manifest={"steps": []},
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-detail",
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

    detail = client.get("/api/runs/run-detail", headers=api_headers)
    assert detail.status_code == 200
    assert detail.json()["run"]["run_id"] == "run-detail"

    missing_run = client.get("/api/runs/missing-run", headers=api_headers)
    assert missing_run.status_code == 404

    articles = client.get("/api/articles", headers=api_headers)
    assert any(a["run_id"] == "run-detail" for a in articles.json()["articles"])

    article = client.get("/api/articles/run-detail", headers=api_headers)
    assert article.status_code == 200

    missing_article = client.get("/api/articles/missing", headers=api_headers)
    assert missing_article.status_code == 404

    step_file_missing = client.get("/api/runs/run-detail/step-files/missing.md", headers=api_headers)
    assert step_file_missing.status_code == 404

    stop_completed = client.post("/api/runs/run-detail/stop", headers=api_headers)
    assert stop_completed.status_code == 200
    assert stop_completed.json()["ok"] is False

    deleted = client.delete("/api/runs/run-detail", headers=api_headers)
    assert deleted.status_code == 200

    delete_running = client.delete("/api/runs/run-running", headers=api_headers)
    assert delete_running.status_code == 404


def test_active_overview_and_stats(client, api_headers) -> None:
    overview = client.get("/api/active/overview", headers=api_headers)
    assert overview.status_code == 200

    stats = client.get("/api/stats", headers=api_headers)
    assert stats.status_code == 200


def test_publish_run_missing_paths(client, api_headers) -> None:
    missing_run = client.post("/api/runs/missing/publish", headers=api_headers)
    assert missing_run.status_code == 404

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-no-article", topic_slug="sports", status="completed"))
        db.commit()
    finally:
        db.close()

    missing_article = client.post("/api/runs/run-no-article/publish", headers=api_headers)
    assert missing_article.status_code == 404
