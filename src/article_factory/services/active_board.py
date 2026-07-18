from __future__ import annotations

from sqlalchemy.orm import Session

from article_factory.models import (
    FactoryRun,
    FlowQueue,
    ShiftAssignment,
    ShiftDeskSlot,
    ShiftPlan,
    TopicQueueItem,
)
from article_factory.schemas import RunSummary
from article_factory.services.flow_queues import DEFAULT_QUEUE_SLUG
from article_factory.services.flow_steps import flow_steps_payload_for_run
from article_factory.services.shift_plans import shift_plan_payload
from article_factory.services.shift_windows import SHIFT_LABELS


def enriched_run_summary(db: Session, run: FactoryRun, *, include_steps: bool = False) -> dict:
    from article_factory.services.flow_versions import get_flow_version
    from article_factory.services.step_trace import step_executions_payload

    item = db.get(TopicQueueItem, run.queue_item_id) if run.queue_item_id else None
    assignment = db.get(ShiftAssignment, run.shift_assignment_id) if run.shift_assignment_id else None
    desk_slot = (
        db.get(ShiftDeskSlot, assignment.shift_desk_slot_id)
        if assignment is not None
        else None
    )
    shift_plan = db.get(ShiftPlan, run.shift_plan_id) if run.shift_plan_id else None

    queue_name: str | None = None
    flow_queue_id: int | None = None
    if item is not None:
        flow_queue_id = item.flow_queue_id
        if item.flow_queue_id is not None:
            queue = db.get(FlowQueue, item.flow_queue_id)
            queue_name = queue.name if queue else None
    elif desk_slot is not None:
        queue_name = desk_slot.name or desk_slot.desk_path

    summary = RunSummary.model_validate(run).model_dump()
    summary["started_at"] = run.started_at.isoformat() if run.started_at else None
    summary["finished_at"] = run.finished_at.isoformat() if run.finished_at else None
    summary["topic_prompt"] = (
        assignment.prompt if assignment else (item.prompt if item else (run.topic_prompt or None))
    )
    summary["flow_queue_id"] = flow_queue_id
    summary["flow_queue_name"] = queue_name or "Unassigned"
    summary["shift_plan_id"] = run.shift_plan_id
    summary["shift_assignment_id"] = run.shift_assignment_id
    summary["shift_desk_name"] = desk_slot.name if desk_slot else None
    summary["shift_label"] = (
        SHIFT_LABELS.get(shift_plan.shift_key, shift_plan.shift_key)
        if shift_plan is not None
        else None
    )
    summary["flow_steps"] = flow_steps_payload_for_run(db, run)
    if run.flow_version_id:
        version = get_flow_version(db, run.flow_version_id)
        if version:
            summary["flow_version_number"] = version.version_number
            summary["flow_version_message"] = version.message
    if include_steps:
        summary["steps"] = step_executions_payload(db, run.run_id)
    else:
        summary["steps"] = []
    return summary


def _queue_counts(db: Session, queue_id: int) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    queue = db.get(FlowQueue, queue_id)
    query = db.query(TopicQueueItem.status)
    if queue is not None and queue.slug == DEFAULT_QUEUE_SLUG:
        query = query.filter(
            (TopicQueueItem.flow_queue_id == queue_id) | (TopicQueueItem.flow_queue_id.is_(None))
        )
    else:
        query = query.filter_by(flow_queue_id=queue_id)
    for status, in query.all():
        key = status if status in counts else "queued"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _desk_assignment_counts(db: Session, desk_slot_id: int) -> dict[str, int]:
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    for (status,) in db.query(ShiftAssignment.status).filter_by(shift_desk_slot_id=desk_slot_id).all():
        mapped = "queued" if status == "pending" else status
        key = mapped if mapped in counts else "queued"
        counts[key] = counts.get(key, 0) + 1
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

    active_plan = (
        db.query(ShiftPlan)
        .filter_by(status="active")
        .order_by(ShiftPlan.activated_at.desc())
        .first()
    )

    groups: dict[tuple[int | None, str, str], dict] = {}

    def ensure_group(queue_id: int | None, flow_path: str, model: str, *, queue_name: str) -> dict:
        key = (queue_id, flow_path or "", model or "—")
        if key in groups:
            return groups[key]
        counts = _queue_counts(db, queue_id) if queue_id is not None else {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        row = {
            "queue_id": queue_id,
            "queue_name": queue_name,
            "queue_slug": None,
            "flow_path": flow_path or "",
            "model": model or "—",
            "running_count": 0,
            "queued_count": counts["queued"],
            "runs": [],
            "shift_desk_slot_id": queue_id if active_plan is not None else None,
        }
        groups[key] = row
        return row

    if active_plan is not None:
        desks = (
            db.query(ShiftDeskSlot)
            .filter_by(shift_plan_id=active_plan.id)
            .order_by(ShiftDeskSlot.dispatch_order, ShiftDeskSlot.id)
            .all()
        )
        model = (active_plan.default_model or "—").strip() or "—"
        for desk in desks:
            counts = _desk_assignment_counts(db, desk.id)
            row = ensure_group(desk.id, desk.desk_path, model, queue_name=desk.name or desk.desk_path)
            row["queued_count"] = counts["queued"]
            row["queue_slug"] = active_plan.shift_key

        for run in running_runs:
            if run.shift_plan_id != active_plan.id:
                continue
            summary = enriched_run_summary(db, run, include_steps=True)
            desk_id = None
            if run.shift_assignment_id:
                assignment = db.get(ShiftAssignment, run.shift_assignment_id)
                desk_id = assignment.shift_desk_slot_id if assignment else None
            desk = db.get(ShiftDeskSlot, desk_id) if desk_id else None
            group = ensure_group(
                desk_id,
                summary.get("flow_path") or (desk.desk_path if desk else ""),
                summary.get("selected_model") or model,
                queue_name=(desk.name if desk else summary.get("flow_queue_name") or "Shift desk"),
            )
            if summary.get("selected_model"):
                group["model"] = summary["selected_model"]
            group["runs"].append(summary)
            group["running_count"] = len(group["runs"])
    else:
        queues = db.query(FlowQueue).order_by(FlowQueue.dispatch_order, FlowQueue.id).all()
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
            row = ensure_group(queue.id, queue.flow_path, model, queue_name=queue.name)
            row["queue_slug"] = queue.slug

        for run in running_runs:
            if run.shift_plan_id is not None:
                continue
            summary = enriched_run_summary(db, run, include_steps=True)
            group = ensure_group(
                summary.get("flow_queue_id"),
                summary.get("flow_path") or "",
                summary.get("selected_model") or "—",
                queue_name=summary.get("flow_queue_name") or "Unassigned",
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

    history = []
    history_ids = [run.run_id for run in history_runs]
    from article_factory.services.step_trace import batch_step_executions_payload

    history_steps = batch_step_executions_payload(db, history_ids)
    for run in history_runs:
        row = enriched_run_summary(db, run, include_steps=False)
        row["steps"] = history_steps.get(run.run_id, [])
        history.append(row)

    return {
        "running_groups": running_groups,
        "history_runs": history,
        "active_shift": shift_plan_payload(db, active_plan) if active_plan else None,
    }
