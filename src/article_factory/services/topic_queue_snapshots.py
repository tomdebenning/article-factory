from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FlowQueue, TopicQueueItem, TopicQueueSnapshot


def _topics_payload(items: list[TopicQueueItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "topic_slug": item.topic_slug,
            "prompt": item.prompt,
            "status": item.status,
            "flow_path": item.flow_path,
        }
        for item in items
    ]


def topic_queue_content_hash(topics: list[dict[str, Any]]) -> str:
    payload = json.dumps(topics, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def get_or_create_topic_queue_snapshot(
    db: Session,
    *,
    flow_queue_id: int | None,
    queue_item_id: int | None = None,
) -> TopicQueueSnapshot | None:
    resolved_queue_id = flow_queue_id
    if resolved_queue_id is None and queue_item_id is not None:
        item = db.get(TopicQueueItem, queue_item_id)
        if item:
            resolved_queue_id = item.flow_queue_id

    if resolved_queue_id is None:
        return None

    items = (
        db.query(TopicQueueItem)
        .filter_by(flow_queue_id=resolved_queue_id)
        .order_by(TopicQueueItem.id.asc())
        .all()
    )
    if not items:
        return None

    topics = _topics_payload(items)
    digest = topic_queue_content_hash(topics)
    existing = (
        db.query(TopicQueueSnapshot)
        .filter_by(flow_queue_id=resolved_queue_id, content_hash=digest)
        .first()
    )
    if existing:
        return existing

    queue = db.get(FlowQueue, resolved_queue_id)
    row = TopicQueueSnapshot(
        flow_queue_id=resolved_queue_id,
        queue_slug=queue.slug if queue else "",
        queue_name=queue.name if queue else "",
        content_hash=digest,
        topics=topics,
        topic_count=len(topics),
    )
    db.add(row)
    db.flush()
    return row


def snapshot_to_dict(row: TopicQueueSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "flow_queue_id": row.flow_queue_id,
        "queue_slug": row.queue_slug,
        "queue_name": row.queue_name,
        "topic_count": row.topic_count,
        "content_hash": row.content_hash,
        "topics": row.topics or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_topic_queue_snapshots_for_flow_queue(db: Session, flow_queue_id: int) -> list[TopicQueueSnapshot]:
    return (
        db.query(TopicQueueSnapshot)
        .filter_by(flow_queue_id=flow_queue_id)
        .order_by(TopicQueueSnapshot.created_at.desc())
        .all()
    )
