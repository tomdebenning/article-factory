from __future__ import annotations

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, FlowQueue, TopicQueueItem
from article_factory.schemas import RunSummary
from article_factory.services.flow_steps import flow_steps_payload


def enriched_run_summary(db: Session, run: FactoryRun, *, include_steps: bool = False) -> dict:
    from article_factory.services.step_trace import step_executions_payload

    item = db.get(TopicQueueItem, run.queue_item_id) if run.queue_item_id else None
    queue_name: str | None = None
    flow_queue_id: int | None = None
    if item is not None:
        flow_queue_id = item.flow_queue_id
        if item.flow_queue_id is not None:
            queue = db.get(FlowQueue, item.flow_queue_id)
            queue_name = queue.name if queue else None

    summary = RunSummary.model_validate(run).model_dump()
    summary["started_at"] = run.started_at.isoformat() if run.started_at else None
    summary["finished_at"] = run.finished_at.isoformat() if run.finished_at else None
    summary["topic_prompt"] = item.prompt if item else None
    summary["flow_queue_id"] = flow_queue_id
    summary["flow_queue_name"] = queue_name or "Unassigned"
    summary["flow_steps"] = flow_steps_payload(run.flow_path or "")
    if include_steps:
        summary["steps"] = step_executions_payload(db, run.run_id)
    else:
        summary["steps"] = []
    return summary


def _queue_counts(db: Session, queue_id: int) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    for status, in db.query(TopicQueueItem.status).filter_by(flow_queue_id=queue_id).all():
        if status in counts:
            counts[status] += 1
    return counts


def build_active_overview(db: Session, *, history_limit: int = 250) -> dict:
    capped = max(1, min(history_limit, 500))

    running_runs = (
        db.query(FactoryRun)
        .filter(FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.desc())
        .all()
    )
    history_runs = (
        db.query(FactoryRun)
        .filter(FactoryRun.status != "running")
        .order_by(FactoryRun.finished_at.desc(), FactoryRun.started_at.desc())
        .limit(capped)
        .all()
    )

    queues = db.query(FlowQueue).order_by(FlowQueue.dispatch_order, FlowQueue.id).all()
    groups: dict[tuple[int | None, str, str], dict] = {}

    def ensure_group(queue_id: int | None, flow_path: str, model: str) -> dict:
        key = (queue_id, flow_path or "", model or "—")
        if key in groups:
            return groups[key]
        queue = db.get(FlowQueue, queue_id) if queue_id is not None else None
        counts = _queue_counts(db, queue_id) if queue_id is not None else {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        row = {
            "queue_id": queue_id,
            "queue_name": queue.name if queue else "Unassigned",
            "queue_slug": queue.slug if queue else None,
            "flow_path": flow_path or (queue.flow_path if queue else ""),
            "model": model or "—",
            "running_count": 0,
            "queued_count": counts["queued"],
            "runs": [],
        }
        groups[key] = row
        return row

    for queue in queues:
        counts = _queue_counts(db, queue.id)
        has_active_run = any(
            (item := db.get(TopicQueueItem, run.queue_item_id)) is not None
            and item.flow_queue_id == queue.id
            for run in running_runs
            if run.queue_item_id is not None
        )
        if not counts["queued"] and not has_active_run:
            continue
        model = "—"
        for run in running_runs:
            item = db.get(TopicQueueItem, run.queue_item_id) if run.queue_item_id else None
            if item and item.flow_queue_id == queue.id and run.selected_model:
                model = run.selected_model
                break
        ensure_group(queue.id, queue.flow_path, model)

    for run in running_runs:
        summary = enriched_run_summary(db, run, include_steps=True)
        group = ensure_group(
            summary.get("flow_queue_id"),
            summary.get("flow_path") or "",
            summary.get("selected_model") or "—",
        )
        if summary.get("selected_model"):
            group["model"] = summary["selected_model"]
        group["runs"].append(summary)
        group["running_count"] = len(group["runs"])

    running_groups = sorted(
        groups.values(),
        key=lambda row: (
            -(row["running_count"] + row["queued_count"]),
            row["queue_name"].lower(),
        ),
    )

    history = [enriched_run_summary(db, run) for run in history_runs]

    return {
        "running_groups": running_groups,
        "history_runs": history,
    }
