from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import StepExecution
from article_factory.services.run_recovery import commit_with_retry

_PROGRESS_COMMIT_INTERVAL_SECONDS = 1.5


def duration_ms_between(
    start: datetime | None,
    end: datetime | None = None,
) -> int | None:
    if not start:
        return None
    end = end or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return int((end - start).total_seconds() * 1000)


class StepTracer:
    def __init__(
        self,
        db: Session,
        *,
        run_id: str,
        step_key: str,
        puller: str,
        model: str,
    ) -> None:
        self.db = db
        self._last_progress_commit_at = 0.0
        self.execution = StepExecution(
            run_id=run_id,
            step_key=step_key,
            status="pending",
            puller=puller,
            model=model,
        )
        db.add(self.execution)
        commit_with_retry(db)
        db.refresh(self.execution)

    def _commit_progress(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or (now - self._last_progress_commit_at) >= _PROGRESS_COMMIT_INTERVAL_SECONDS:
            commit_with_retry(self.db)
            self._last_progress_commit_at = now
            if force:
                from article_factory.services.showroom_status_sync import schedule_showroom_status_refresh

                schedule_showroom_status_refresh()

    def _set_progress(self, *, activity: str, cp_round: int | None = None) -> None:
        progress = dict(self.execution.progress or {})
        progress["activity"] = activity
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        if cp_round is not None:
            progress["cp_round"] = cp_round
        self.execution.progress = progress

    def mark_submitted(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        queue_depth: int | None,
        cp_round: int = 1,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.execution.status = "submitted"
        self.execution.agent_id = agent_id
        self.execution.conversation_id = conversation_id
        self.execution.cp_queue_depth = queue_depth
        self.execution.submitted_at = now
        self.execution.turns = cp_round
        self._set_progress(activity="Submitted to control plane", cp_round=cp_round)
        self._commit_progress(force=True)

    def record_cp_round(
        self,
        *,
        cp_round: int,
        agent_id: str,
        conversation_id: str,
        queue_depth: int | None,
    ) -> None:
        """Track a follow-up control-plane round within the same step (tool loop)."""
        now = datetime.now(timezone.utc)
        self.execution.status = "submitted"
        self.execution.agent_id = agent_id
        self.execution.conversation_id = conversation_id
        self.execution.cp_queue_depth = queue_depth
        self.execution.submitted_at = now
        self.execution.turns = cp_round
        self._set_progress(activity=f"Control plane round {cp_round}", cp_round=cp_round)
        self._commit_progress(force=True)

    def mark_waiting(self) -> None:
        if self.execution.status == "submitted":
            self.execution.status = "waiting"
            self._set_progress(
                activity="Waiting for puller",
                cp_round=int((self.execution.progress or {}).get("cp_round") or self.execution.turns or 1),
            )
            self._commit_progress()

    def mark_pulled(self) -> None:
        if self.execution.status in ("submitted", "waiting"):
            self.execution.status = "pulled"
            self.execution.pulled_at = datetime.now(timezone.utc)
        cp_round = int((self.execution.progress or {}).get("cp_round") or self.execution.turns or 1)
        self._set_progress(activity="Puller generating response", cp_round=cp_round)
        self._commit_progress()

    def record_activity(self, activity: str, *, cp_round: int | None = None) -> None:
        resolved_round = cp_round
        if resolved_round is None:
            resolved_round = int((self.execution.progress or {}).get("cp_round") or self.execution.turns or 1)
        self._set_progress(activity=activity, cp_round=resolved_round)
        self._commit_progress()

    def record_task_status(self, task_status: dict[str, Any]) -> None:
        if not isinstance(task_status, dict):
            return
        progress = dict(self.execution.progress or {})
        progress["cp_task_status"] = task_status.get("status")
        progress["cp_task"] = {
            "status": task_status.get("status"),
            "target_puller": task_status.get("target_puller"),
            "fetched_by": task_status.get("fetched_by"),
            "fetched_at": task_status.get("fetched_at"),
            "completed_at": task_status.get("completed_at"),
            "queue_depth_at_submit": task_status.get("queue_depth_at_submit"),
            "response_error": task_status.get("response_error"),
            "response_error_kind": task_status.get("response_error_kind"),
        }
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.execution.progress = progress
        status = str(task_status.get("status") or "")
        if status == "queued":
            depth = task_status.get("queue_depth_at_submit")
            activity = "Queued on puller"
            if depth is not None:
                activity = f"Queued on puller (depth {depth})"
            self._set_progress(activity=activity)
        elif status == "fetched":
            puller = task_status.get("fetched_by") or task_status.get("target_puller") or "puller"
            self._set_progress(activity=f"Fetched by {puller} — generating")
        elif status == "failed" and task_status.get("response_error"):
            kind = task_status.get("response_error_kind") or "error"
            self._set_progress(activity=f"Puller reported {kind}")
        self._commit_progress()

    def record_tool_start(self, tool_name: str, args: dict[str, Any], *, round_num: int) -> None:
        from article_factory.services.tool_usage import summarize_tool_detail, tool_label

        label = tool_label(tool_name)
        detail = summarize_tool_detail(tool_name, args)
        activity = f"Running {label}"
        if detail:
            activity = f"{activity}: {detail}"
        self._set_progress(activity=activity, cp_round=round_num)
        self._commit_progress()

    def append_tool_use(self, entry: dict[str, Any]) -> None:
        tools = list(self.execution.tools_used or [])
        tools.append(entry)
        self.execution.tools_used = tools
        label = str(entry.get("label") or entry.get("tool") or "tool")
        self._set_progress(
            activity=f"Used {label}",
            cp_round=int(entry.get("round") or (self.execution.progress or {}).get("cp_round") or 1),
        )
        self._commit_progress(force=True)

    def mark_completed(
        self,
        *,
        response_content: str | None = None,
        usage: dict | None = None,
        duration_ms: int | None = None,
        tools_used: list[dict] | None = None,
        turns: int | None = None,
    ) -> None:
        self.execution.status = "completed"
        self.execution.completed_at = datetime.now(timezone.utc)
        if response_content is not None:
            self.execution.response_content = response_content
        if usage is not None:
            self.execution.usage = usage
        if tools_used is not None:
            self.execution.tools_used = tools_used
        if turns is not None:
            self.execution.turns = turns
        if duration_ms is not None:
            self.execution.duration_ms = duration_ms
        elif self.execution.started_at and self.execution.completed_at:
            self.execution.duration_ms = duration_ms_between(
                self.execution.started_at,
                self.execution.completed_at,
            )
        cp_round = int((self.execution.progress or {}).get("cp_round") or turns or self.execution.turns or 1)
        self._set_progress(activity="Completed", cp_round=cp_round)
        commit_with_retry(self.db)

    def mark_failed(self, error: str) -> None:
        self.execution.status = "failed"
        self.execution.error = error or "Step failed"
        self.execution.completed_at = datetime.now(timezone.utc)
        cp_round = int((self.execution.progress or {}).get("cp_round") or self.execution.turns or 1)
        self._set_progress(activity="Failed", cp_round=cp_round)
        commit_with_retry(self.db)


def list_step_executions(db: Session, run_id: str) -> list[StepExecution]:
    return (
        db.query(StepExecution)
        .filter_by(run_id=run_id)
        .order_by(StepExecution.started_at.asc(), StepExecution.id.asc())
        .all()
    )


def step_execution_to_dict(step: StepExecution) -> dict:
    return {
        "id": step.id,
        "run_id": step.run_id,
        "step_key": step.step_key,
        "status": step.status,
        "agent_id": step.agent_id,
        "conversation_id": step.conversation_id,
        "puller": step.puller,
        "model": step.model,
        "cp_queue_depth": step.cp_queue_depth,
        "error": step.error,
        "response_content": step.response_content,
        "duration_ms": step.duration_ms,
        "usage": step.usage or {},
        "tools_used": step.tools_used or [],
        "progress": step.progress or {},
        "turns": step.turns,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "submitted_at": step.submitted_at.isoformat() if step.submitted_at else None,
        "pulled_at": step.pulled_at.isoformat() if step.pulled_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
    }


def enrich_steps_with_responses(
    db: Session,
    run_id: str,
    steps: list[dict],
) -> list[dict]:
    """Fill response_content and usage from pipeline_state for incomplete DB rows."""
    from article_factory.models import FactoryRun

    run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
    records: list[dict] = []
    if run is not None:
        if run.pipeline_state:
            records = list(run.pipeline_state.get("step_records") or [])
        elif run.manifest:
            records = list(run.manifest.get("steps") or run.manifest.get("step_stats") or [])

    if records:
        for index, step in enumerate(steps):
            if index >= len(records):
                break
            record = records[index]
            if not step.get("response_content") and record.get("content"):
                step["response_content"] = record["content"]
            if not step.get("duration_ms") and record.get("duration_ms"):
                step["duration_ms"] = record.get("duration_ms")
            if not step.get("usage") and record.get("usage"):
                step["usage"] = record.get("usage")
            if not step.get("tools_used") and record.get("tools_used"):
                step["tools_used"] = record.get("tools_used")
            if step.get("turns") is None and record.get("turns") is not None:
                step["turns"] = record.get("turns")

    missing = [s for s in steps if not s.get("response_content") and s.get("status") == "completed"]
    if not missing or run is None or not run.pipeline_state:
        return steps

    by_key: dict[str, list[dict]] = {}
    for record in run.pipeline_state.get("step_records") or []:
        key = str(record.get("step_key") or "")
        by_key.setdefault(key, []).append(record)

    seen: dict[str, int] = {}
    for step in steps:
        if step.get("response_content"):
            continue
        key = str(step.get("step_key") or "")
        bucket = by_key.get(key, [])
        idx = seen.get(key, 0)
        seen[key] = idx + 1
        if idx < len(bucket) and bucket[idx].get("content"):
            step["response_content"] = bucket[idx]["content"]
    return steps


def step_executions_payload(db: Session, run_id: str) -> list[dict]:
    steps = [step_execution_to_dict(s) for s in list_step_executions(db, run_id)]
    return enrich_steps_with_responses(db, run_id, steps)


def batch_step_executions_payload(db: Session, run_ids: list[str]) -> dict[str, list[dict]]:
    if not run_ids:
        return {}
    rows = (
        db.query(StepExecution)
        .filter(StepExecution.run_id.in_(run_ids))
        .order_by(StepExecution.run_id.asc(), StepExecution.started_at.asc(), StepExecution.id.asc())
        .all()
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.run_id, []).append(step_execution_to_dict(row))
    for run_id, steps in grouped.items():
        grouped[run_id] = enrich_steps_with_responses(db, run_id, steps)
    return grouped
