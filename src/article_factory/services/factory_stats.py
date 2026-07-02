from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, StepExecution, TopicQueueItem


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def summarize_durations(durations: list[int]) -> dict[str, int]:
    if not durations:
        return {
            "count": 0,
            "total_duration_ms": 0,
            "avg_duration_ms": 0,
            "median_duration_ms": 0,
        }
    total = sum(durations)
    count = len(durations)
    return {
        "count": count,
        "total_duration_ms": total,
        "avg_duration_ms": round(total / count),
        "median_duration_ms": _median(durations),
    }


def _prompt_for_run(run: FactoryRun, queue_item: TopicQueueItem | None) -> str:
    if queue_item and str(queue_item.prompt or "").strip():
        return str(queue_item.prompt).strip()
    return run.topic_slug.replace("-", " ").title()


def _collect_step_rows(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(StepExecution, FactoryRun, TopicQueueItem)
        .join(FactoryRun, FactoryRun.run_id == StepExecution.run_id)
        .outerjoin(TopicQueueItem, TopicQueueItem.id == FactoryRun.queue_item_id)
        .filter(StepExecution.status == "completed")
        .filter(StepExecution.duration_ms.isnot(None))
        .filter(StepExecution.duration_ms > 0)
        .order_by(StepExecution.completed_at.desc(), StepExecution.id.desc())
        .all()
    )

    collected: list[dict[str, Any]] = []
    for step, run, queue_item in rows:
        puller = str(step.puller or run.selected_puller or "").strip() or "—"
        model = str(step.model or run.selected_model or "").strip() or "—"
        duration_ms = int(step.duration_ms or 0)
        if duration_ms <= 0:
            continue
        collected.append(
            {
                "step_execution_id": step.id,
                "run_id": step.run_id,
                "step_key": step.step_key,
                "puller": puller,
                "model": model,
                "duration_ms": duration_ms,
                "turns": int(step.turns or 0) or None,
                "prompt": _prompt_for_run(run, queue_item),
                "topic_slug": run.topic_slug,
                "flow_path": run.flow_path,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            }
        )
    return collected


def _group_stats(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[int]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "—") for field in key_fields)
        buckets.setdefault(key, []).append(int(row["duration_ms"]))

    grouped: list[dict[str, Any]] = []
    for key, durations in sorted(buckets.items(), key=lambda item: (-sum(item[1]), item[0])):
        entry = summarize_durations(durations)
        for index, field in enumerate(key_fields):
            entry[field] = key[index]
        grouped.append(entry)
    return grouped


def build_factory_stats(db: Session, *, recent_limit: int = 50) -> dict[str, Any]:
    rows = _collect_step_rows(db)
    durations = [int(row["duration_ms"]) for row in rows]

    return {
        "summary": summarize_durations(durations),
        "by_puller": _group_stats(rows, ("puller",)),
        "by_model": _group_stats(rows, ("model",)),
        "by_step": _group_stats(rows, ("step_key",)),
        "by_puller_step": _group_stats(rows, ("puller", "step_key")),
        "by_model_step": _group_stats(rows, ("model", "step_key")),
        "recent_steps": rows[: max(1, min(recent_limit, 200))],
    }
