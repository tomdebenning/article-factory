from __future__ import annotations

import pytest

from article_factory.services.run_control import request_run_cancel, take_requeue_flow_path

ALT_FLOW_PATH = "test/alt.flow.json"


def _create_alt_flow(client, api_headers) -> None:
    response = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "test", "slug": "alt", "display_name": "Alt flow", "step_count": 1},
    )
    assert response.status_code == 200
    assert response.json()["path"] == ALT_FLOW_PATH


@pytest.mark.asyncio
async def test_switch_flow_clears_history(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem

    db = SessionLocal()
    try:
        item = TopicQueueItem(
            topic_slug="sports",
            prompt="Switch me",
            status="running",
            flow_path="sports/standard-4-step.flow.json",
            priority=50,
        )
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-switch",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
                flow_path="sports/standard-4-step.flow.json",
            )
        )
        db.add(
            FactoryRun(
                run_id="run-done",
                topic_slug="sports",
                status="completed",
                flow_path="sports/standard-4-step.flow.json",
            )
        )
        db.add(
            CompletedArticle(
                run_id="run-done",
                topic_slug="sports",
                title="Keep me",
                summary="Summary",
                body_markdown="# Keep me\n\nBody",
            )
        )
        db.add(
            TopicQueueItem(
                topic_slug="general",
                prompt="Old queued topic",
                status="queued",
                flow_path="sports/standard-4-step.flow.json",
            )
        )
        db.commit()
    finally:
        db.close()

    _create_alt_flow(client, api_headers)

    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={"flow_path": ALT_FLOW_PATH},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["flow_path"] == ALT_FLOW_PATH
    assert body["clear_history"] is True
    assert body["needs_start_prompt"] is True
    assert body["stopped_runs"] == 1
    assert body["cleared"]["cleared_queue_items"] == 2

    listing = client.get("/api/queue", headers=api_headers)
    assert listing.json()["items"] == []

    articles = client.get("/api/articles", headers=api_headers)
    assert articles.status_code == 200
    assert any(item["run_id"] == "run-done" for item in articles.json()["articles"])


@pytest.mark.asyncio
async def test_switch_flow_with_start_prompt(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import TopicQueueItem

    db = SessionLocal()
    try:
        db.add(
            TopicQueueItem(
                topic_slug="general",
                prompt="Old topic",
                status="queued",
                flow_path="sports/standard-4-step.flow.json",
            )
        )
        db.commit()
    finally:
        db.close()

    _create_alt_flow(client, api_headers)

    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={
            "flow_path": ALT_FLOW_PATH,
            "start_prompt": "A fresh article about robotics",
            "topic_slug": "tech",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["needs_start_prompt"] is False
    assert body["queued_item_id"] is not None

    listing = client.get("/api/queue", headers=api_headers)
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["prompt"] == "A fresh article about robotics"
    assert items[0]["flow_path"] == ALT_FLOW_PATH
    assert items[0]["topic_slug"] == "tech"


@pytest.mark.asyncio
async def test_switch_flow_updates_queued_items_without_clear(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import TopicQueueItem

    db = SessionLocal()
    try:
        db.add(
            TopicQueueItem(
                topic_slug="general",
                prompt="Waiting",
                status="queued",
                flow_path="sports/standard-4-step.flow.json",
            )
        )
        db.commit()
    finally:
        db.close()

    _create_alt_flow(client, api_headers)

    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={
            "flow_path": ALT_FLOW_PATH,
            "clear_history": False,
            "update_queued": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["updated_queued_items"] == 1

    listing = client.get("/api/queue", headers=api_headers)
    item = next(i for i in listing.json()["items"] if i["prompt"] == "Waiting")
    assert item["flow_path"] == ALT_FLOW_PATH

@pytest.mark.asyncio
async def test_stop_run_marks_cancelled(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun

    db = SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-stop", topic_slug="sports", status="running", current_step="writer"))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/runs/run-stop/stop", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["run"]["status"] == "cancelled"

    db = SessionLocal()
    try:
        run = db.query(FactoryRun).filter_by(run_id="run-stop").one()
        assert run.status == "cancelled"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_stop_all_runs(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun, TopicQueueItem

    db = SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Active", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-a",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
            )
        )
        db.add(FactoryRun(run_id="run-b", topic_slug="sports", status="completed", current_step="writer"))
        db.commit()
        item_id = item.id
    finally:
        db.close()

    response = client.post("/api/factory/stop-all-runs", headers=api_headers, json={})
    assert response.status_code == 200
    body = response.json()
    assert body["stopped"] == 1
    assert body["run_ids"] == ["run-a"]

    db = SessionLocal()
    try:
        run = db.query(FactoryRun).filter_by(run_id="run-a").one()
        assert run.status == "cancelled"
        item = db.get(TopicQueueItem, item_id)
        assert item is not None
        assert item.status == "failed"
    finally:
        db.close()

    overview = client.get("/api/active/overview", headers=api_headers)
    assert overview.status_code == 200
    assert overview.json()["running_groups"] == []


def test_reconcile_stale_running_queue_items(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun, TopicQueueItem
    from article_factory.services.run_control import reconcile_stale_running_queue_items

    db = SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Stale", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-stale",
                topic_slug="sports",
                queue_item_id=item.id,
                status="cancelled",
                current_step="writer",
            )
        )
        db.commit()
        fixed = reconcile_stale_running_queue_items(db)
        db.commit()
        db.refresh(item)
        assert fixed == 1
        assert item.status == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_switch_flow_unknown_file(client, api_headers) -> None:
    response = client.post(
        "/api/factory/switch-flow",
        headers=api_headers,
        json={"flow_path": "missing/nowhere.flow.json"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_requeue_flow_path_tracking() -> None:
    await request_run_cancel("run-x", requeue_flow_path=ALT_FLOW_PATH)
    assert await take_requeue_flow_path("run-x") == ALT_FLOW_PATH
    assert await take_requeue_flow_path("run-x") is None
