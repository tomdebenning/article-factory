from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.orchestrator.runner import factory_loop
from article_factory.routes.admin import _queue_item_payload, require_api_key
from article_factory.schemas import (
    FlowQueueBody,
    FlowQueueEnqueueBody,
    FlowQueueStartBody,
    FlowQueueUpdateBody,
    QueuePresetBody,
)
from article_factory.services.flow_queues import (
    create_flow_queue,
    delete_flow_queue,
    enqueue_topics_to_queue,
    ensure_default_flow_queue,
    flow_queue_payload,
    list_flow_queues,
    stop_and_clear_flow_queue,
    update_flow_queue,
)
from article_factory.services.queue_presets import (
    delete_queue_preset,
    list_queue_presets,
    read_queue_preset,
    write_queue_preset,
)
from article_factory.services.runtime_settings import update_factory_settings

router = APIRouter(prefix="/api/flow-queues", dependencies=[Depends(require_api_key)])


@router.get("")
def get_flow_queues(db: Session = Depends(get_db)) -> dict:
    return {"queues": list_flow_queues(db)}


@router.get("/presets")
def get_queue_presets(db: Session = Depends(get_db)) -> dict:
    return {"presets": list_queue_presets(db)}


@router.get("/presets/{slug}")
def get_queue_preset(slug: str, db: Session = Depends(get_db)) -> dict:
    try:
        return {"preset": read_queue_preset(db, slug)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/presets")
def post_queue_preset(body: QueuePresetBody, db: Session = Depends(get_db)) -> dict:
    try:
        preset = write_queue_preset(db, body.model_dump())
        db.commit()
        return {"preset": preset}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/presets/{slug}")
def remove_queue_preset(slug: str, db: Session = Depends(get_db)) -> dict:
    try:
        deleted = delete_queue_preset(db, slug)
        db.commit()
        return {"ok": True, **deleted}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/start")
def start_flow_queue(body: FlowQueueStartBody, db: Session = Depends(get_db)) -> dict:
    raise HTTPException(
        status_code=410,
        detail="Legacy queue start is retired. Plan and activate a shift under /api/shifts instead.",
    )


@router.post("")
def post_flow_queue(body: FlowQueueBody, db: Session = Depends(get_db)) -> dict:
    try:
        queue = create_flow_queue(
            db,
            name=body.name,
            flow_path=body.flow_path,
            topic_slug=body.topic_slug,
            slug=body.slug,
        )
        if not body.enabled:
            queue.enabled = False
        db.commit()
        db.refresh(queue)
        return {"queue": flow_queue_payload(db, queue)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{queue_id}")
def put_flow_queue(queue_id: int, body: FlowQueueUpdateBody, db: Session = Depends(get_db)) -> dict:
    try:
        queue = update_flow_queue(
            db,
            queue_id,
            name=body.name,
            flow_path=body.flow_path,
            topic_slug=body.topic_slug,
            enabled=body.enabled,
            dispatch_order=body.dispatch_order,
        )
        db.commit()
        db.refresh(queue)
        return {"queue": flow_queue_payload(db, queue)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{queue_id}")
def remove_flow_queue(queue_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        result = delete_flow_queue(db, queue_id)
        db.commit()
        return {"ok": True, **result}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{queue_id}/stop-and-clear")
async def post_flow_queue_stop_and_clear(queue_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return await stop_and_clear_flow_queue(db, queue_id=queue_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{queue_id}/items")
def get_flow_queue_items(queue_id: int, db: Session = Depends(get_db)) -> dict:
    from article_factory.models import FlowQueue, TopicQueueItem

    queue = db.get(FlowQueue, queue_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Flow queue not found")
    items = (
        db.query(TopicQueueItem)
        .filter_by(flow_queue_id=queue_id)
        .order_by(TopicQueueItem.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "queue": flow_queue_payload(db, queue),
        "items": [_queue_item_payload(db, item) for item in items],
    }


@router.post("/{queue_id}/enqueue")
def post_flow_queue_enqueue(
    queue_id: int,
    body: FlowQueueEnqueueBody,
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = enqueue_topics_to_queue(db, queue_id, body.topics, priority=body.priority)
        db.commit()
        if created:
            factory_loop.request_dispatch()
        return {
            "count": len(created),
            "items": [_queue_item_payload(db, item) for item in created],
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ensure-default")
def post_ensure_default_queue(db: Session = Depends(get_db)) -> dict:
    queue = ensure_default_flow_queue(db)
    db.commit()
    return {"queue": flow_queue_payload(db, queue)}
