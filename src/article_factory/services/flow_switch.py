from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from article_factory.models import CompletedArticle, FactoryRun, StepExecution, TopicQueueItem
from article_factory.services.flow_storage import read_flow
from article_factory.services.run_control import (
    clear_run_cancel,
    mark_run_cancelled_in_db,
    reconcile_stale_running_queue_items,
    reassert_runs_stopped,
    request_run_cancel,
)
from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings


def _validate_flow_path(flow_path: str) -> str:
    path = flow_path.strip()
    if not path:
        raise ValueError("flow_path is required")
    read_flow(path)
    return path


async def stop_all_runs(
    db: Session,
    *,
    requeue: bool = False,
    flow_path: str | None = None,
) -> dict:
    """Stop every running factory run and cancel its background worker."""
    from article_factory.orchestrator.runner import factory_loop

    resolved_flow: str | None = None
    if requeue:
        if not flow_path:
            raise ValueError("flow_path is required when requeue is true")
        resolved_flow = _validate_flow_path(flow_path)

    running_runs = db.query(FactoryRun).filter_by(status="running").all()
    run_ids: list[str] = []
    queue_item_ids: list[int] = []
    hard_stop = not requeue

    for run in running_runs:
        requeue_path = resolved_flow if requeue else None
        await request_run_cancel(run.run_id, requeue_flow_path=requeue_path)
        run_ids.append(run.run_id)
        if run.queue_item_id is not None:
            queue_item_ids.append(run.queue_item_id)
        if hard_stop:
            mark_run_cancelled_in_db(db, run)

    if running_runs:
        db.commit()

    cancelled_workers = 0
    if hard_stop:
        if running_runs:
            cancelled_workers = factory_loop.cancel_run_workers(
                run_ids=run_ids,
                queue_item_ids=queue_item_ids,
            )
            reassert_runs_stopped(db, run_ids)
            reconcile_stale_running_queue_items(db)
            db.commit()
            for run_id in run_ids:
                run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
                if run is not None and run.status != "running":
                    await clear_run_cancel(run_id)
        else:
            factory_loop.clear_reserved_pullers()
            reconcile_stale_running_queue_items(db)
            db.commit()
        factory_loop.request_dispatch()

    return {
        "ok": True,
        "stopped": len(run_ids),
        "run_ids": run_ids,
        "cancelled_workers": cancelled_workers,
        "message": (
            f"Stopped {len(run_ids)} run(s)."
            if hard_stop and run_ids
            else (
                "Stop requested — active runs will halt at the next step boundary."
                if run_ids
                else "No active runs to stop."
            )
        ),
    }


def clear_factory_history(db: Session) -> dict:
    """Remove queue items and finished runs so a new flow can start fresh."""
    running_runs = db.query(FactoryRun).filter_by(status="running").all()
    for run in running_runs:
        run.queue_item_id = None

    queue_count = db.query(TopicQueueItem).count()
    db.query(TopicQueueItem).delete()

    finished_run_ids = [
        row[0]
        for row in db.query(FactoryRun.run_id).filter(FactoryRun.status != "running").all()
    ]
    deleted_runs = len(finished_run_ids)
    if finished_run_ids:
        db.query(StepExecution).filter(StepExecution.run_id.in_(finished_run_ids)).delete(
            synchronize_session=False
        )
        db.query(FactoryRun).filter(FactoryRun.run_id.in_(finished_run_ids)).delete(
            synchronize_session=False
        )

    db.commit()
    return {
        "cleared_queue_items": queue_count,
        "deleted_runs": deleted_runs,
        "running_runs_left": len(running_runs),
    }


def _enqueue_start_prompt(
    db: Session,
    *,
    flow_path: str,
    start_prompt: str,
    topic_slug: str,
) -> int:
    prompt = start_prompt.strip()
    if not prompt:
        raise ValueError("start_prompt is required")
    item = TopicQueueItem(
        topic_slug=topic_slug.strip() or "general",
        prompt=prompt,
        flow_path=flow_path,
        status="queued",
        priority=0,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item.id


async def switch_active_flow(
    db: Session,
    *,
    flow_path: str,
    set_as_default: bool = True,
    clear_history: bool = True,
    update_queued: bool = False,
    requeue_running: bool = False,
    start_prompt: str | None = None,
    topic_slug: str = "general",
) -> dict:
    """Stop running flows, optionally clear queue history, and apply a new flow file."""
    resolved = _validate_flow_path(flow_path)

    if set_as_default:
        runtime = load_runtime_settings(db)
        update_factory_settings(
            db,
            {
                "control_plane_url": runtime.control_plane_url,
                "cms_url": runtime.cms_url,
                "cms_api_key": runtime.cms_api_key,
                "default_puller": runtime.default_puller,
                "default_model": runtime.default_model,
                "default_flow_path": resolved,
            },
        )

    stop_result = await stop_all_runs(
        db,
        requeue=requeue_running and not clear_history,
        flow_path=resolved if requeue_running and not clear_history else None,
    )

    cleared: dict | None = None
    updated_items = 0
    if clear_history:
        cleared = clear_factory_history(db)
    elif update_queued:
        for item in (
            db.query(TopicQueueItem)
            .filter(TopicQueueItem.status.in_(("queued", "running")))
            .all()
        ):
            item.flow_path = resolved
            updated_items += 1
        db.commit()

    queued_item_id: int | None = None
    if start_prompt and start_prompt.strip():
        queued_item_id = _enqueue_start_prompt(
            db,
            flow_path=resolved,
            start_prompt=start_prompt,
            topic_slug=topic_slug,
        )

    if clear_history:
        message = (
            f"Switched to {resolved} and cleared queue history. "
            f"{stop_result['stopped']} run(s) stopping."
            if stop_result["stopped"]
            else f"Switched to {resolved} and cleared queue history."
        )
        if queued_item_id is not None:
            message += " Your new topic is queued."
    elif stop_result["stopped"]:
        message = (
            f"Switched to {resolved}. "
            f"{stop_result['stopped']} run(s) stopping; "
            f"{updated_items} queue topic(s) updated. "
            "Stopped topics will re-enter the queue with the new flow."
        )
    else:
        message = f"Switched to {resolved}. {updated_items} queue topic(s) updated."

    return {
        "ok": True,
        "flow_path": resolved,
        "set_as_default": set_as_default,
        "clear_history": clear_history,
        "cleared": cleared,
        "updated_queued_items": updated_items,
        "stopped_runs": stop_result["stopped"],
        "stopped_run_ids": stop_result["run_ids"],
        "queued_item_id": queued_item_id,
        "needs_start_prompt": clear_history and queued_item_id is None,
        "message": message,
    }
