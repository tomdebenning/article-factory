from __future__ import annotations

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, ShiftAssignment, TopicQueueItem


def factory_queue_depth(db: Session) -> int:
    """Articles waiting for factory capacity (shift queue, topic queue, or extra running runs)."""
    shift_pending = db.query(ShiftAssignment).filter_by(status="pending").count()
    topic_queued = db.query(TopicQueueItem).filter_by(status="queued").count()
    running_count = db.query(FactoryRun).filter_by(status="running").count()
    waiting_runs = max(0, running_count - 1)
    return shift_pending + topic_queued + waiting_runs
