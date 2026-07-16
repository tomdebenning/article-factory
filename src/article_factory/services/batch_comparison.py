from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, TopicQueueSnapshot
from article_factory.services.flow_performance import _aggregate_row, _snapshot_label
from article_factory.services.run_error_classification import (
    error_group_label,
    load_manual_error_tags,
    resolve_run_error_group,
    step_errors_for_run,
)
from article_factory.services.run_turn_metrics import review_cycles_for_run, step_turns_for_run, turn_metrics_for_runs
from article_factory.services.topic_queue_snapshots import snapshot_to_dict
from article_factory.services.turn_outcome_charts import build_turn_outcome_charts


def _error_groups(runs: list[FactoryRun], db: Session, manual_tags: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[FactoryRun]] = {}
    for run in runs:
        info = resolve_run_error_group(
            run,
            manual_tags=manual_tags,
            step_errors=step_errors_for_run(db, run.run_id) if run.status == "failed" else None,
        )
        group = str(info["error_group"])
        grouped.setdefault(group, []).append(run)

    rows = []
    for group, group_runs in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        rows.append(
            {
                "error_group": group,
                "error_group_label": error_group_label(group),
                "count": len(group_runs),
                "run_ids": [run.run_id for run in group_runs[:50]],
            }
        )
    return rows


def _topic_row(
    db: Session,
    *,
    topic: dict[str, Any],
    run: FactoryRun | None,
    manual_tags: dict[str, Any],
) -> dict[str, Any]:
    queue_item_id = topic.get("id")
    if run is None:
        return {
            "queue_item_id": queue_item_id,
            "topic_slug": topic.get("topic_slug") or "",
            "prompt_preview": str(topic.get("prompt") or "")[:160],
            "run_id": None,
            "status": topic.get("status") or "queued",
            "error_group": "queued",
            "error_group_label": error_group_label("queued"),
            "review_rounds": None,
            "total_step_turns": None,
            "first_pass_accept": None,
            "selected_model": None,
            "manual_note": None,
        }

    error_info = resolve_run_error_group(
        run,
        manual_tags=manual_tags,
        step_errors=step_errors_for_run(db, run.run_id) if run.status == "failed" else None,
    )
    turns = step_turns_for_run(db, run)
    return {
        "queue_item_id": queue_item_id,
        "topic_slug": run.topic_slug,
        "prompt_preview": str(topic.get("prompt") or "")[:160],
        "run_id": run.run_id,
        "status": run.status,
        "error_group": error_info["error_group"],
        "error_group_label": error_info["error_group_label"],
        "auto_error_group": error_info["auto_error_group"],
        "error_message": error_info["error_message"],
        "manual_tag": error_info["manual_tag"],
        "manual_note": error_info["manual_note"],
        "review_rounds": review_cycles_for_run(run, db),
        "review_cycles": review_cycles_for_run(run, db),
        "total_step_turns": turns["total_step_turns"],
        "step_turns_by_step": turns["by_step_avg_turns"],
        "first_pass_accept": run.first_pass_accept,
        "selected_model": run.selected_model,
        "selected_puller": run.selected_puller,
        "flow_version_id": run.flow_version_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def build_batch_comparison(
    db: Session,
    *,
    topic_queue_snapshot_id: int,
    flow_version_id: int | None = None,
    selected_model: str | None = None,
    selected_puller: str | None = None,
) -> dict[str, Any]:
    snapshot = db.get(TopicQueueSnapshot, topic_queue_snapshot_id)
    if snapshot is None:
        raise ValueError("Topic queue snapshot not found")

    query = db.query(FactoryRun).filter(FactoryRun.topic_queue_snapshot_id == topic_queue_snapshot_id)
    if flow_version_id is not None:
        query = query.filter(FactoryRun.flow_version_id == flow_version_id)
    if selected_model:
        query = query.filter(FactoryRun.selected_model == selected_model)
    if selected_puller:
        query = query.filter(FactoryRun.selected_puller == selected_puller)

    runs = query.order_by(FactoryRun.started_at.asc()).all()
    run_ids = [run.run_id for run in runs]
    manual_tags = load_manual_error_tags(db, run_ids)

    runs_by_queue_item: dict[int, FactoryRun] = {}
    for run in runs:
        if run.queue_item_id is not None:
            runs_by_queue_item[int(run.queue_item_id)] = run

    topics = list(snapshot.topics or [])
    topic_rows = [
        _topic_row(
            db,
            topic=topic,
            run=runs_by_queue_item.get(int(topic.get("id") or 0)),
            manual_tags=manual_tags,
        )
        for topic in topics
    ]

    aggregate = _aggregate_row(runs)
    aggregate.update(turn_metrics_for_runs(runs, db))
    aggregate["failure_count"] = sum(1 for run in runs if run.status == "failed")
    aggregate["failure_rate"] = (aggregate["failure_count"] / len(runs)) if runs else None

    return {
        "snapshot": snapshot_to_dict(snapshot),
        "flow_path": runs[0].flow_path if runs else None,
        "filters": {
            "topic_queue_snapshot_id": topic_queue_snapshot_id,
            "flow_version_id": flow_version_id,
            "selected_model": selected_model,
            "selected_puller": selected_puller,
        },
        "summary": aggregate,
        "error_groups": _error_groups(runs, db, manual_tags),
        "turn_charts": build_turn_outcome_charts(runs, db),
        "topics": topic_rows,
        "runs": [row for row in topic_rows if row.get("run_id")],
    }


def list_batches_for_flow_version(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int | None = None,
    selected_model: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(FactoryRun).filter(FactoryRun.flow_path == flow_path)
    if flow_version_id is not None:
        query = query.filter(FactoryRun.flow_version_id == flow_version_id)
    if selected_model:
        query = query.filter(FactoryRun.selected_model == selected_model)

    snapshot_ids = {
        row[0]
        for row in query.with_entities(FactoryRun.topic_queue_snapshot_id).distinct().all()
        if row[0] is not None
    }

    batches: list[dict[str, Any]] = []
    for snapshot_id in sorted(snapshot_ids, reverse=True):
        snapshot_runs = [
            run
            for run in query.filter(FactoryRun.topic_queue_snapshot_id == snapshot_id).all()
        ]
        if not snapshot_runs:
            continue
        summary = _aggregate_row(snapshot_runs)
        summary.update(turn_metrics_for_runs(snapshot_runs, db))
        summary["failure_count"] = sum(1 for run in snapshot_runs if run.status == "failed")
        batches.append(
            {
                "topic_queue_snapshot_id": snapshot_id,
                "queue_name": _snapshot_label(db, snapshot_id),
                **summary,
            }
        )
    return batches
