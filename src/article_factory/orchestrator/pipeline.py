from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.services.tool_usage import aggregate_tool_use_by_step
from article_factory.services.iteration_stats import attach_iteration_metadata


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def build_manifest(run: FactoryRun, steps: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = {
        "run_id": run.run_id,
        "topic_slug": run.topic_slug,
        "flow_path": run.flow_path,
        "draft_number": run.draft_number,
        "review_round": run.review_round,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "selected_model": run.selected_model,
        "selected_puller": run.selected_puller,
        "steps": steps,
        "stats": _aggregate_stats(steps),
        "step_stats": steps,
        "tool_use": aggregate_tool_use_by_step(steps),
    }
    return attach_iteration_metadata(
        manifest,
        draft_number=run.draft_number,
        review_round=run.review_round,
    )


def _aggregate_stats(steps: list[dict[str, Any]]) -> dict[str, Any]:
    from article_factory.services.token_usage import aggregate_usage_stats

    return aggregate_usage_stats(steps)


async def push_factory_status(
    cms: Any,
    *,
    db: Session | None = None,
    state: str,
    active_run: FactoryRun | None,
    active_runs: list[FactoryRun] | None = None,
    queue_depth: int,
    topic_slug: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "state": state,
        "queue_depth": queue_depth,
        "topic_slug": topic_slug,
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    runs = active_runs if active_runs is not None else ([active_run] if active_run else [])
    if db is not None and runs:
        payload["active_runs"] = [serialize_active_run(db, run) for run in runs[:3]]
        payload["active_run"] = payload["active_runs"][0]
    elif active_run:
        payload["active_run"] = {
            "run_id": active_run.run_id,
            "topic_slug": active_run.topic_slug,
            "flow_path": active_run.flow_path,
            "current_step": active_run.current_step,
            "draft_number": active_run.draft_number,
            "review_round": active_run.review_round,
            "started_at": active_run.started_at.isoformat() if active_run.started_at else None,
        }
        payload["active_runs"] = [payload["active_run"]]
    else:
        payload["active_run"] = None
        payload["active_runs"] = []
    await cms.put_factory_status(payload)


def serialize_active_run(db: Session, run: FactoryRun) -> dict[str, Any]:
    from article_factory.services.flow_steps import flow_steps_payload
    from article_factory.services.step_trace import step_executions_payload

    topic_prompt: str | None = None
    if run.queue_item_id:
        item = db.get(TopicQueueItem, run.queue_item_id)
        if item and item.prompt.strip():
            topic_prompt = item.prompt

    steps: list[dict[str, Any]] = []
    try:
        steps = [
            {
                "step_key": step["step_key"],
                "status": step["status"],
                "progress": step.get("progress") or {},
                "tools_used": step.get("tools_used") or [],
                "puller": step.get("puller") or "",
                "model": step.get("model") or "",
            }
            for step in step_executions_payload(db, run.run_id)
        ]
    except Exception:
        if run.current_step:
            steps = [{"step_key": run.current_step, "status": "pulled", "progress": {}, "tools_used": []}]
    return {
        "run_id": run.run_id,
        "topic_slug": run.topic_slug,
        "topic_prompt": topic_prompt,
        "current_step": run.current_step,
        "draft_number": run.draft_number,
        "review_round": run.review_round,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "flow_path": run.flow_path,
        "flow_steps": flow_steps_payload(run.flow_path or ""),
        "steps": steps,
    }
