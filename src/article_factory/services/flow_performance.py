from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, FlowVersion, TopicQueueSnapshot
from article_factory.services.flow_schema import FlowDefinition
from article_factory.services.flow_storage import read_flow
from article_factory.services.flow_versions import load_version_flow
from article_factory.services.run_error_classification import (
    load_manual_error_tags,
    resolve_run_error_group,
    step_errors_for_run,
)
from article_factory.services.run_turn_metrics import review_cycles_for_run, step_turns_for_run, turn_metrics_for_runs
from article_factory.services.topic_queue_snapshots import snapshot_to_dict
from article_factory.services.turn_outcome_charts import build_turn_outcome_charts
from article_factory.services.verdict import Verdict, parse_verdict


from article_factory.services.flow_roles import resolve_gate_config  # noqa: F401 — re-exported


def compute_first_pass_accept(flow: FlowDefinition, step_records: list[dict[str, Any]]) -> bool:
    gate_key, _producer_keys = resolve_gate_config(flow)
    if not gate_key:
        keys = [str(record.get("step_key") or "") for record in step_records]
        return len(keys) > 0 and len(keys) == len(set(keys))

    gate_records = [record for record in step_records if str(record.get("step_key") or "") == gate_key]
    if len(gate_records) != 1:
        return False
    content = str(gate_records[0].get("content") or gate_records[0].get("response_content") or "")
    return parse_verdict(content) == Verdict.ACCEPT


def compute_first_pass_from_manifest(manifest: dict[str, Any], flow: FlowDefinition) -> bool:
    steps = list(manifest.get("step_stats") or manifest.get("steps") or [])
    return compute_first_pass_accept(flow, steps)


def _run_flow_definition(run: FactoryRun, db: Session) -> FlowDefinition:
    if run.flow_version_id:
        version = db.get(FlowVersion, run.flow_version_id)
        if version and version.flow_content:
            return load_version_flow(version)
    return read_flow(run.flow_path)


def apply_run_performance(db: Session, run: FactoryRun, step_records: list[dict[str, Any]]) -> None:
    try:
        flow = _run_flow_definition(run, db)
    except Exception:
        run.first_pass_accept = None
        return
    run.first_pass_accept = compute_first_pass_accept(flow, step_records)


def _aggregate_row(runs: list[FactoryRun]) -> dict[str, Any]:
    completed = [run for run in runs if run.status == "completed"]
    first_pass = sum(1 for run in completed if run.first_pass_accept is True)
    tokens = 0
    for run in completed:
        stats = (run.manifest or {}).get("stats") or {}
        tokens += int(stats.get("total_tokens") or 0)
    run_total = len(runs)
    completed_total = len(completed)
    return {
        "run_count": run_total,
        "completed_count": completed_total,
        "completion_rate": (completed_total / run_total) if run_total else None,
        "first_pass_count": first_pass,
        "first_pass_yield_rate": (first_pass / run_total) if run_total else None,
        "first_pass_completed_rate": (first_pass / completed_total) if completed_total else None,
        # Backwards-compatible alias: first-pass share among completed runs
        "first_pass_rate": (first_pass / completed_total) if completed_total else None,
        "first_pass_scored_count": completed_total,
        "avg_tokens": (tokens / completed_total) if completed_total else None,
    }


def aggregate_performance(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int | None = None,
    topic_queue_snapshot_id: int | None = None,
    selected_model: str | None = None,
) -> dict[str, Any]:
    query = db.query(FactoryRun).filter(FactoryRun.flow_path == flow_path)
    if flow_version_id is not None:
        query = query.filter(FactoryRun.flow_version_id == flow_version_id)
    if topic_queue_snapshot_id is not None:
        query = query.filter(FactoryRun.topic_queue_snapshot_id == topic_queue_snapshot_id)
    if selected_model:
        query = query.filter(FactoryRun.selected_model == selected_model)

    runs = query.order_by(FactoryRun.started_at.desc()).all()
    overall = _aggregate_row(runs)
    overall.update(turn_metrics_for_runs(runs, db))
    overall["failure_count"] = sum(1 for run in runs if run.status == "failed")
    overall["failure_rate"] = (overall["failure_count"] / len(runs)) if runs else None
    manual_tags = load_manual_error_tags(db, [run.run_id for run in runs])
    overall["error_groups"] = _error_group_summary(runs, db, manual_tags)
    overall["turn_charts"] = build_turn_outcome_charts(runs, db)

    by_version: dict[int, dict[str, Any]] = {}
    for run in runs:
        key = run.flow_version_id or 0
        by_version.setdefault(key, []).append(run)

    by_queue: dict[int, dict[str, Any]] = {}
    for run in runs:
        key = run.topic_queue_snapshot_id or 0
        by_queue.setdefault(key, []).append(run)

    by_model: dict[str, list[FactoryRun]] = {}
    for run in runs:
        model = run.selected_model or "—"
        by_model.setdefault(model, []).append(run)

    return {
        "flow_path": flow_path,
        "overall": overall,
        "by_version": [
            {
                "flow_version_id": version_id or None,
                **_aggregate_row(version_runs),
            }
            for version_id, version_runs in sorted(by_version.items(), key=lambda item: item[0], reverse=True)
        ],
        "by_topic_queue": [
            {
                "topic_queue_snapshot_id": snapshot_id or None,
                **_aggregate_row(queue_runs),
                **turn_metrics_for_runs(queue_runs, db),
                "failure_count": sum(1 for run in queue_runs if run.status == "failed"),
                "queue_name": _snapshot_label(db, snapshot_id),
            }
            for snapshot_id, queue_runs in sorted(by_queue.items(), key=lambda item: item[0], reverse=True)
        ],
        "batches": list_batches_for_flow(
            db,
            flow_path=flow_path,
            flow_version_id=flow_version_id,
            selected_model=selected_model,
        ),
        "by_model": [
            {"model": model, **_aggregate_row(model_runs)}
            for model, model_runs in sorted(by_model.items(), key=lambda item: item[0])
        ],
        "runs": [_run_summary(run, db, manual_tags) for run in runs[:100]],
    }


def _error_group_summary(
    runs: list[FactoryRun],
    db: Session,
    manual_tags: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, int] = {}
    for run in runs:
        info = resolve_run_error_group(
            run,
            manual_tags=manual_tags,
            step_errors=step_errors_for_run(db, run.run_id) if run.status == "failed" else None,
        )
        group = str(info["error_group"])
        grouped[group] = grouped.get(group, 0) + 1
    from article_factory.services.run_error_classification import error_group_label

    return [
        {"error_group": group, "error_group_label": error_group_label(group), "count": count}
        for group, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


def list_batches_for_flow(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int | None = None,
    selected_model: str | None = None,
) -> list[dict[str, Any]]:
    from article_factory.services.batch_comparison import list_batches_for_flow_version

    return list_batches_for_flow_version(
        db,
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        selected_model=selected_model,
    )


def _snapshot_label(db: Session, snapshot_id: int) -> str | None:
    if not snapshot_id:
        return None
    row = db.get(TopicQueueSnapshot, snapshot_id)
    if not row:
        return None
    return row.queue_name or row.queue_slug or f"Topic queue #{snapshot_id}"


def _run_summary(run: FactoryRun, db: Session | None = None, manual_tags: dict[str, Any] | None = None) -> dict[str, Any]:
    production = (run.manifest or {}).get("production") or {}
    payload: dict[str, Any] = {
        "run_id": run.run_id,
        "topic_slug": run.topic_slug,
        "status": run.status,
        "flow_version_id": run.flow_version_id,
        "topic_queue_snapshot_id": run.topic_queue_snapshot_id,
        "selected_model": run.selected_model,
        "first_pass_accept": run.first_pass_accept,
        "draft_number": run.draft_number,
        "review_round": run.review_round,
        "iteration_count": production.get("iteration_count"),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    if db is not None:
        payload["review_rounds"] = review_cycles_for_run(run, db)
        payload["review_cycles"] = payload["review_rounds"]
        payload["total_step_turns"] = step_turns_for_run(db, run)["total_step_turns"]
        error_info = resolve_run_error_group(
            run,
            manual_tags=manual_tags or {},
            step_errors=step_errors_for_run(db, run.run_id) if run.status == "failed" else None,
        )
        payload.update(error_info)
    return payload


def list_topic_queues_for_flow(db: Session, flow_path: str) -> list[dict[str, Any]]:
    snapshot_ids = {
        row[0]
        for row in db.query(FactoryRun.topic_queue_snapshot_id)
        .filter(FactoryRun.flow_path == flow_path, FactoryRun.topic_queue_snapshot_id.isnot(None))
        .distinct()
        .all()
        if row[0] is not None
    }
    rows = [db.get(TopicQueueSnapshot, snapshot_id) for snapshot_id in snapshot_ids]
    return [snapshot_to_dict(row) for row in rows if row is not None]
