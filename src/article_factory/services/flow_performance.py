from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, FlowVersion, TopicQueueSnapshot
from article_factory.services.flow_schema import FlowDefinition
from article_factory.services.flow_storage import read_flow
from article_factory.services.flow_versions import load_version_flow
from article_factory.services.topic_queue_snapshots import snapshot_to_dict
from article_factory.services.verdict import Verdict, parse_verdict


def resolve_gate_config(flow: FlowDefinition) -> tuple[str | None, list[str]]:
    if flow.performance and flow.performance.gate_step_key:
        gate_key = flow.performance.gate_step_key.strip()
        producers = list(flow.performance.producer_step_keys or [])
        if gate_key and not producers:
            producers = _default_producer_keys(flow, gate_key)
        return gate_key or None, producers

    steps = sorted(flow.steps, key=lambda step: step.order)
    if not steps:
        return None, []

    last = steps[-1]
    completion = last.completion
    if not completion or not completion.can_loop:
        return None, []

    gate_key = last.step_key
    goto_id = completion.loop_goto_step_id
    if not goto_id:
        return gate_key, [steps[0].step_key]

    goto_order = next((step.order for step in steps if step.step_id == goto_id), 1)
    producer_keys = [step.step_key for step in steps if step.order >= goto_order]
    return gate_key, producer_keys


def _default_producer_keys(flow: FlowDefinition, gate_key: str) -> list[str]:
    steps = sorted(flow.steps, key=lambda step: step.order)
    gate_order = next((step.order for step in steps if step.step_key == gate_key), len(steps))
    return [step.step_key for step in steps if step.order <= gate_order]


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
    with_metric = [run for run in completed if run.first_pass_accept is not None]
    first_pass = sum(1 for run in with_metric if run.first_pass_accept)
    tokens = 0
    for run in completed:
        stats = (run.manifest or {}).get("stats") or {}
        tokens += int(stats.get("total_tokens") or 0)
    return {
        "run_count": len(runs),
        "completed_count": len(completed),
        "first_pass_count": first_pass,
        "first_pass_rate": (first_pass / len(with_metric)) if with_metric else None,
        "avg_tokens": (tokens / len(completed)) if completed else None,
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
                "queue_name": _snapshot_label(db, snapshot_id),
            }
            for snapshot_id, queue_runs in sorted(by_queue.items(), key=lambda item: item[0], reverse=True)
        ],
        "by_model": [
            {"model": model, **_aggregate_row(model_runs)}
            for model, model_runs in sorted(by_model.items(), key=lambda item: item[0])
        ],
        "runs": [_run_summary(run) for run in runs[:100]],
    }


def _snapshot_label(db: Session, snapshot_id: int) -> str | None:
    if not snapshot_id:
        return None
    row = db.get(TopicQueueSnapshot, snapshot_id)
    if not row:
        return None
    return row.queue_name or row.queue_slug or f"Topic queue #{snapshot_id}"


def _run_summary(run: FactoryRun) -> dict[str, Any]:
    production = (run.manifest or {}).get("production") or {}
    return {
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
