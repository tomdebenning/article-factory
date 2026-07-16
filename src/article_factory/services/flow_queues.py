from __future__ import annotations

import re

from sqlalchemy.orm import Session

from article_factory.models import CompletedArticle, FactoryRun, FlowQueue, StepExecution, TopicQueueItem
from article_factory.services.flow_paths import resolve_default_flow_path

DEFAULT_QUEUE_SLUG = "default"
_null_queue_migration_done = False


def slugify_queue_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "queue"


def ensure_default_flow_queue(db: Session) -> FlowQueue:
    global _null_queue_migration_done
    row = db.query(FlowQueue).filter_by(slug=DEFAULT_QUEUE_SLUG).one_or_none()
    if row is None:
        row = FlowQueue(
            slug=DEFAULT_QUEUE_SLUG,
            name="Default",
            flow_path=resolve_default_flow_path(db),
            topic_slug="general",
            enabled=True,
            dispatch_order=0,
        )
        db.add(row)
        db.flush()
    if not _null_queue_migration_done:
        has_null = (
            db.query(TopicQueueItem.id)
            .filter(TopicQueueItem.flow_queue_id.is_(None))
            .limit(1)
            .first()
            is not None
        )
        if has_null:
            db.query(TopicQueueItem).filter(TopicQueueItem.flow_queue_id.is_(None)).update(
                {TopicQueueItem.flow_queue_id: row.id},
                synchronize_session=False,
            )
        _null_queue_migration_done = True
    return row


def resolve_queue_flow_path(db: Session, queue: FlowQueue) -> str:
    cleaned = (queue.flow_path or "").strip()
    if cleaned:
        return cleaned
    return resolve_default_flow_path(db)


def _queue_counts(db: Session, queue_id: int) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    queue = db.get(FlowQueue, queue_id)
    query = db.query(TopicQueueItem.status, TopicQueueItem.id)
    if queue is not None and queue.slug == DEFAULT_QUEUE_SLUG:
        query = query.filter(
            (TopicQueueItem.flow_queue_id == queue_id) | (TopicQueueItem.flow_queue_id.is_(None))
        )
    else:
        query = query.filter_by(flow_queue_id=queue_id)
    rows = query.all()
    for status, _item_id in rows:
        key = status if status in counts else "queued"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _active_run_for_queue(db: Session, queue_id: int) -> FactoryRun | None:
    running_items = (
        db.query(TopicQueueItem.id)
        .filter_by(flow_queue_id=queue_id, status="running")
        .all()
    )
    item_ids = [row[0] for row in running_items]
    if not item_ids:
        return None
    return (
        db.query(FactoryRun)
        .filter(FactoryRun.queue_item_id.in_(item_ids), FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.desc())
        .first()
    )


def flow_queue_payload(db: Session, queue: FlowQueue) -> dict:
    counts = _queue_counts(db, queue.id)
    active = _active_run_for_queue(db, queue.id)
    return {
        "id": queue.id,
        "slug": queue.slug,
        "name": queue.name,
        "flow_path": queue.flow_path,
        "flow_version_id": queue.flow_version_id,
        "topic_slug": queue.topic_slug,
        "enabled": queue.enabled,
        "dispatch_order": queue.dispatch_order,
        "created_at": queue.created_at.isoformat() if queue.created_at else None,
        "counts": counts,
        "active_run_id": active.run_id if active else None,
    }


def list_flow_queues(db: Session) -> list[dict]:
    ensure_default_flow_queue(db)
    queues = db.query(FlowQueue).order_by(FlowQueue.dispatch_order, FlowQueue.id).all()
    return [flow_queue_payload(db, queue) for queue in queues]


def create_flow_queue(
    db: Session,
    *,
    name: str,
    flow_path: str,
    topic_slug: str = "general",
    slug: str = "",
) -> FlowQueue:
    ensure_default_flow_queue(db)
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("Queue name is required")

    base_slug = slugify_queue_name(slug.strip() or cleaned_name)
    candidate = base_slug
    suffix = 2
    while db.query(FlowQueue).filter_by(slug=candidate).one_or_none() is not None:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1

    max_order = db.query(FlowQueue.dispatch_order).order_by(FlowQueue.dispatch_order.desc()).first()
    next_order = (max_order[0] + 1) if max_order else 0

    queue = FlowQueue(
        slug=candidate,
        name=cleaned_name,
        flow_path=(flow_path or "").strip() or resolve_default_flow_path(db),
        topic_slug=(topic_slug or "general").strip() or "general",
        enabled=True,
        dispatch_order=next_order,
    )
    db.add(queue)
    db.flush()
    return queue


def update_flow_queue(
    db: Session,
    queue_id: int,
    *,
    name: str | None = None,
    flow_path: str | None = None,
    topic_slug: str | None = None,
    enabled: bool | None = None,
    dispatch_order: int | None = None,
    flow_version_id: int | None = None,
) -> FlowQueue:
    queue = db.get(FlowQueue, queue_id)
    if queue is None:
        raise LookupError("Flow queue not found")
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Queue name cannot be empty")
        queue.name = cleaned
    if flow_path is not None:
        cleaned = flow_path.strip()
        queue.flow_path = cleaned or resolve_default_flow_path(db)
    if topic_slug is not None:
        queue.topic_slug = (topic_slug.strip() or "general")
    if enabled is not None:
        queue.enabled = enabled
    if dispatch_order is not None:
        queue.dispatch_order = dispatch_order
    if flow_version_id is not None:
        queue.flow_version_id = flow_version_id if flow_version_id > 0 else None
    db.flush()
    return queue


def _queue_item_scope_filter(query, queue: FlowQueue):
    if queue.slug == DEFAULT_QUEUE_SLUG:
        return query.filter(
            (TopicQueueItem.flow_queue_id == queue.id) | (TopicQueueItem.flow_queue_id.is_(None))
        )
    return query.filter(TopicQueueItem.flow_queue_id == queue.id)


def _queue_item_ids(db: Session, queue: FlowQueue) -> list[int]:
    return [
        row[0]
        for row in _queue_item_scope_filter(db.query(TopicQueueItem.id), queue).all()
    ]


def _delete_finished_runs(db: Session, run_ids: list[str]) -> int:
    if not run_ids:
        return 0
    db.query(StepExecution).filter(StepExecution.run_id.in_(run_ids)).delete(
        synchronize_session=False
    )
    db.query(CompletedArticle).filter(CompletedArticle.run_id.in_(run_ids)).delete(
        synchronize_session=False
    )
    return (
        db.query(FactoryRun)
        .filter(FactoryRun.run_id.in_(run_ids), FactoryRun.status != "running")
        .delete(synchronize_session=False)
    )


async def stop_and_clear_flow_queue(db: Session, *, queue_id: int) -> dict:
    """Stop active runs for a flow queue, remove queued topics, and drop stopped runs from history."""
    from article_factory.orchestrator.runner import factory_loop
    from article_factory.services.run_control import (
        clear_run_cancel,
        mark_run_cancelled_in_db,
        reconcile_stale_running_queue_items,
        reassert_runs_stopped,
        request_run_cancel,
    )

    queue = db.get(FlowQueue, queue_id)
    if queue is None:
        raise LookupError("Flow queue not found")

    item_ids = _queue_item_ids(db, queue)

    running_runs: list[FactoryRun] = []
    if item_ids:
        running_runs = (
            db.query(FactoryRun)
            .filter(FactoryRun.status == "running", FactoryRun.queue_item_id.in_(item_ids))
            .all()
        )

    run_ids: list[str] = []
    queue_item_ids: list[int] = []
    for run in running_runs:
        await request_run_cancel(run.run_id)
        run_ids.append(run.run_id)
        if run.queue_item_id is not None:
            queue_item_ids.append(run.queue_item_id)
        mark_run_cancelled_in_db(db, run)

    if running_runs:
        db.commit()
        factory_loop.cancel_run_workers(run_ids=run_ids, queue_item_ids=queue_item_ids)
        reassert_runs_stopped(db, run_ids)
        reconcile_stale_running_queue_items(db)
        db.commit()
        for run_id in run_ids:
            run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
            if run is not None and run.status != "running":
                await clear_run_cancel(run_id)

    if item_ids:
        remaining = (
            db.query(FactoryRun)
            .filter(FactoryRun.status == "running", FactoryRun.queue_item_id.in_(item_ids))
            .all()
        )
        for run in remaining:
            if run.run_id in run_ids:
                continue
            await request_run_cancel(run.run_id)
            run_ids.append(run.run_id)
            if run.queue_item_id is not None:
                queue_item_ids.append(run.queue_item_id)
            mark_run_cancelled_in_db(db, run)
        if remaining:
            db.commit()
            factory_loop.cancel_run_workers(run_ids=[r.run_id for r in remaining], queue_item_ids=queue_item_ids)
            reassert_runs_stopped(db, [r.run_id for r in remaining])
            reconcile_stale_running_queue_items(db)
            db.commit()

    cleared_queued = 0
    if item_ids:
        cleared_queued = (
            _queue_item_scope_filter(db.query(TopicQueueItem), queue)
            .filter(TopicQueueItem.status == "queued")
            .delete(synchronize_session=False)
        )

    # Drop stuck queue items and remove stopped runs so they no longer appear in Active.
    cleared_pending_items = 0
    if item_ids:
        cleared_pending_items = (
            _queue_item_scope_filter(db.query(TopicQueueItem), queue)
            .filter(TopicQueueItem.status.in_(("running", "failed")))
            .delete(synchronize_session=False)
        )

    deleted_runs = _delete_finished_runs(db, run_ids)

    if item_ids:
        stale_run_ids = [
            row[0]
            for row in db.query(FactoryRun.run_id)
            .filter(
                FactoryRun.queue_item_id.in_(item_ids),
                FactoryRun.status.in_(("cancelled", "failed")),
            )
            .all()
        ]
        deleted_runs += _delete_finished_runs(db, stale_run_ids)

    reconcile_stale_running_queue_items(db)
    db.commit()
    factory_loop.request_dispatch()

    return {
        "ok": True,
        "queue_id": queue_id,
        "queue_name": queue.name,
        "stopped_runs": len(run_ids),
        "stopped_run_ids": run_ids,
        "deleted_runs": deleted_runs,
        "cleared_queued_items": cleared_queued,
        "cleared_pending_items": cleared_pending_items,
        "message": (
            f'Cleared {cleared_queued} queued topic(s), stopped {len(run_ids)} running article(s), '
            f'and removed {deleted_runs} stopped run(s) from queue "{queue.name}".'
            if cleared_queued or run_ids or cleared_pending_items
            else f'Queue "{queue.name}" had nothing to stop or clear.'
        ),
    }


def delete_flow_queue(db: Session, queue_id: int) -> dict:
    queue = db.get(FlowQueue, queue_id)
    if queue is None:
        raise LookupError("Flow queue not found")
    if queue.slug == DEFAULT_QUEUE_SLUG:
        raise ValueError("The default queue cannot be deleted")

    running = (
        db.query(TopicQueueItem)
        .filter_by(flow_queue_id=queue_id, status="running")
        .count()
    )
    if running:
        raise ValueError("Stop active runs in this queue before deleting it")

    deleted_items = (
        db.query(TopicQueueItem)
        .filter_by(flow_queue_id=queue_id)
        .delete(synchronize_session=False)
    )
    payload = flow_queue_payload(db, queue)
    db.delete(queue)
    db.flush()
    return {"deleted": payload, "deleted_items": deleted_items}


def enqueue_topics_to_queue(
    db: Session,
    queue_id: int,
    topics: list[str],
    *,
    priority: int = 100,
) -> list[TopicQueueItem]:
    queue = db.get(FlowQueue, queue_id)
    if queue is None:
        raise LookupError("Flow queue not found")
    if not queue.enabled:
        raise ValueError("Enable this queue before adding topics")

    flow_path = resolve_queue_flow_path(db, queue)
    created: list[TopicQueueItem] = []
    for index, line in enumerate(topics):
        prompt = line.strip()
        if not prompt:
            continue
        item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug=queue.topic_slug,
            flow_path=flow_path,
            prompt=prompt,
            priority=priority + index,
        )
        db.add(item)
        db.flush()
        created.append(item)
    return created


def select_queued_items_round_robin(
    db: Session,
    *,
    limit: int,
    start_index: int,
) -> tuple[list[TopicQueueItem], int]:
    if limit <= 0:
        return [], start_index

    queues = (
        db.query(FlowQueue)
        .filter_by(enabled=True)
        .order_by(FlowQueue.dispatch_order, FlowQueue.id)
        .all()
    )
    default_queue = ensure_default_flow_queue(db)
    if not queues:
        items = (
            db.query(TopicQueueItem)
            .filter(
                TopicQueueItem.status == "queued",
                (TopicQueueItem.flow_queue_id == default_queue.id)
                | (TopicQueueItem.flow_queue_id.is_(None)),
            )
            .order_by(TopicQueueItem.priority, TopicQueueItem.created_at)
            .limit(limit)
            .all()
        )
        return items, start_index

    picked: list[TopicQueueItem] = []
    picked_ids: set[int] = set()
    queue_count = len(queues)
    index = start_index % queue_count

    def _next_item_for_queue(queue: FlowQueue) -> TopicQueueItem | None:
        query = db.query(TopicQueueItem).filter(
            TopicQueueItem.status == "queued",
        )
        if queue.id == default_queue.id:
            query = query.filter(
                (TopicQueueItem.flow_queue_id == queue.id) | (TopicQueueItem.flow_queue_id.is_(None))
            )
        else:
            query = query.filter(TopicQueueItem.flow_queue_id == queue.id)
        for item in query.order_by(TopicQueueItem.priority, TopicQueueItem.created_at).all():
            if item.id not in picked_ids:
                return item
        return None

    while len(picked) < limit:
        progress = False
        for offset in range(queue_count):
            queue = queues[(index + offset) % queue_count]
            item = _next_item_for_queue(queue)
            if item is None or item.id in picked_ids:
                continue
            picked.append(item)
            picked_ids.add(item.id)
            progress = True
            if len(picked) >= limit:
                break
        if not progress:
            break
        index = (index + 1) % queue_count

    return picked, index
