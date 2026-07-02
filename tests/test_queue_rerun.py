from __future__ import annotations

import article_factory.db as db_module
from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.services.queue_retry import is_queue_item_rerunnable


def test_rerunnable_failed_item() -> None:
    item = TopicQueueItem(topic_slug="sports", prompt="x", status="failed")
    run = FactoryRun(run_id="run-1", topic_slug="sports", status="failed")
    assert is_queue_item_rerunnable(item, run) is True


def test_rerunnable_completed_item() -> None:
    item = TopicQueueItem(topic_slug="sports", prompt="x", status="completed")
    run = FactoryRun(run_id="run-1", topic_slug="sports", status="completed")
    assert is_queue_item_rerunnable(item, run) is True


def test_rerunnable_cancelled_run() -> None:
    item = TopicQueueItem(topic_slug="sports", prompt="x", status="failed")
    run = FactoryRun(run_id="run-1", topic_slug="sports", status="cancelled")
    assert is_queue_item_rerunnable(item, run) is True


def test_not_rerunnable_while_running() -> None:
    item = TopicQueueItem(topic_slug="sports", prompt="x", status="running")
    run = FactoryRun(run_id="run-1", topic_slug="sports", status="running")
    assert is_queue_item_rerunnable(item, run) is False


def test_not_rerunnable_while_queued() -> None:
    item = TopicQueueItem(topic_slug="sports", prompt="x", status="queued")
    assert is_queue_item_rerunnable(item, None) is False


def test_rerunnable_stale_running_queue_item(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="x", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-stale",
            topic_slug="sports",
            queue_item_id=item.id,
            status="cancelled",
        )
        db.add(run)
        db.commit()
        assert is_queue_item_rerunnable(item, run) is True
    finally:
        db.close()
