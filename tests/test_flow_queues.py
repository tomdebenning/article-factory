from __future__ import annotations

from article_factory.models import FlowQueue, TopicQueueItem
from article_factory.services.flow_queues import (
    create_flow_queue,
    enqueue_topics_to_queue,
    ensure_default_flow_queue,
    select_queued_items_round_robin,
)


def test_create_and_enqueue_flow_queue(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        queue = create_flow_queue(
            db,
            name="Sports",
            flow_path="sports/standard-4-step.flow.json",
            topic_slug="sports",
        )
        db.commit()

        created = enqueue_topics_to_queue(db, queue.id, ["Topic A", "Topic B"])
        db.commit()
        assert len(created) == 2
        assert created[0].flow_path == "sports/standard-4-step.flow.json"
        assert created[0].topic_slug == "sports"
        assert created[0].flow_queue_id == queue.id
    finally:
        db.close()


def test_select_queued_items_round_robin(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        queue_a = create_flow_queue(
            db,
            name="Queue A",
            flow_path="sports/standard-4-step.flow.json",
            topic_slug="sports",
        )
        queue_b = create_flow_queue(
            db,
            name="Queue B",
            flow_path="test/SimpleTest.flow.json",
            topic_slug="general",
        )
        db.add(TopicQueueItem(flow_queue_id=queue_a.id, topic_slug="sports", prompt="A1", status="queued"))
        db.add(TopicQueueItem(flow_queue_id=queue_a.id, topic_slug="sports", prompt="A2", status="queued"))
        db.add(TopicQueueItem(flow_queue_id=queue_b.id, topic_slug="general", prompt="B1", status="queued"))
        db.commit()

        picked, next_index = select_queued_items_round_robin(db, limit=2, start_index=0)
        assert len(picked) == 2
        prompts = [item.prompt for item in picked]
        assert prompts[0] == "A1"
        assert prompts[1] == "B1"
        assert next_index == 1

        for item in picked:
            item.status = "running"
        db.commit()
        picked2, _ = select_queued_items_round_robin(db, limit=2, start_index=next_index)
        assert [item.prompt for item in picked2] == ["A2"]
    finally:
        db.close()


def test_select_skips_already_picked_in_same_queue(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        default = ensure_default_flow_queue(db)
        db.add(
            TopicQueueItem(
                flow_queue_id=default.id,
                topic_slug="general",
                prompt="One",
                status="queued",
            )
        )
        db.add(
            TopicQueueItem(
                flow_queue_id=default.id,
                topic_slug="general",
                prompt="Two",
                status="queued",
            )
        )
        db.commit()
        picked, _ = select_queued_items_round_robin(db, limit=2, start_index=0)
        assert [item.prompt for item in picked] == ["One", "Two"]
    finally:
        db.close()


def test_flow_queues_api(client, api_headers, configured_db) -> None:
    created = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={
            "name": "Tech",
            "flow_path": "test/SimpleTest.flow.json",
            "topic_slug": "tech",
        },
    )
    assert created.status_code == 200
    queue_id = created.json()["queue"]["id"]

    enqueued = client.post(
        f"/api/flow-queues/{queue_id}/enqueue",
        headers=api_headers,
        json={"topics": ["An article about AI"]},
    )
    assert enqueued.status_code == 200
    assert enqueued.json()["count"] == 1

    listing = client.get("/api/flow-queues", headers=api_headers)
    assert listing.status_code == 200
    assert any(q["name"] == "Tech" for q in listing.json()["queues"])

    status = client.get("/api/factory/status", headers=api_headers)
    assert status.status_code == 200
    assert any(q["name"] == "Tech" for q in status.json()["flow_queues"])

    default = client.get("/api/flow-queues", headers=api_headers).json()
    assert any(q["slug"] == "default" for q in default["queues"])
