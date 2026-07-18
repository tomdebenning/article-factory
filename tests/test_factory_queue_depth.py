from __future__ import annotations

import uuid

from article_factory import db as db_module
from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.services.factory_queue_depth import factory_queue_depth


def test_factory_queue_depth_counts_topic_queue(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(TopicQueueItem(status="queued", prompt="b", topic_slug="news", flow_path="test.flow.json"))
        db.add(TopicQueueItem(status="queued", prompt="c", topic_slug="news", flow_path="test.flow.json"))
        db.commit()
        assert factory_queue_depth(db) == 2
    finally:
        db.close()


def test_factory_queue_depth_counts_extra_running_runs(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        before = factory_queue_depth(db)
        db.add(FactoryRun(run_id=f"run-{uuid.uuid4().hex[:8]}", topic_slug="news", status="running"))
        db.add(FactoryRun(run_id=f"run-{uuid.uuid4().hex[:8]}", topic_slug="news", status="running"))
        db.add(FactoryRun(run_id=f"run-{uuid.uuid4().hex[:8]}", topic_slug="news", status="running"))
        db.commit()
        assert factory_queue_depth(db) == before + 2
    finally:
        db.close()
