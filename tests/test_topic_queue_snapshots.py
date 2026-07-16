from __future__ import annotations

from article_factory.services.flow_queues import create_flow_queue, enqueue_topics_to_queue
from article_factory.services.topic_queue_snapshots import (
    get_or_create_topic_queue_snapshot,
    list_topic_queue_snapshots_for_flow_queue,
    snapshot_to_dict,
    topic_queue_content_hash,
)


def test_topic_queue_content_hash_stable() -> None:
    topics = [{"id": 1, "topic_slug": "sports", "prompt": "A", "status": "queued", "flow_path": "x.flow.json"}]
    assert topic_queue_content_hash(topics) == topic_queue_content_hash(list(topics))


def test_get_or_create_returns_none_without_queue(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        assert get_or_create_topic_queue_snapshot(db, flow_queue_id=None) is None
    finally:
        db.close()


def test_get_or_create_returns_none_for_empty_queue(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        queue = create_flow_queue(
            db,
            name="Empty",
            flow_path="sports/standard-4-step.flow.json",
            topic_slug="sports",
        )
        db.commit()
        assert get_or_create_topic_queue_snapshot(db, flow_queue_id=queue.id) is None
    finally:
        db.close()


def test_get_or_create_deduplicates_and_resolves_item_id(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        queue = create_flow_queue(
            db,
            name="Sports",
            flow_path="sports/standard-4-step.flow.json",
            topic_slug="sports",
        )
        items = enqueue_topics_to_queue(db, queue.id, ["Topic A"])
        db.commit()

        snap1 = get_or_create_topic_queue_snapshot(db, flow_queue_id=queue.id)
        snap2 = get_or_create_topic_queue_snapshot(db, flow_queue_id=None, queue_item_id=items[0].id)
        db.commit()
        assert snap1 is not None
        assert snap2 is not None
        assert snap1.id == snap2.id

        payload = snapshot_to_dict(snap1)
        assert payload["topic_count"] == len(payload["topics"]) == 1
        assert payload["queue_slug"] == queue.slug

        listed = list_topic_queue_snapshots_for_flow_queue(db, queue.id)
        assert len(listed) == 1
        assert listed[0].id == snap1.id
    finally:
        db.close()
