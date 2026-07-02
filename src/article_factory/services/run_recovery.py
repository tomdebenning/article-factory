from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, StepExecution, TopicQueueItem

logger = logging.getLogger(__name__)

_IN_FLIGHT_STEP_STATUSES = frozenset({"pending", "submitted", "waiting", "pulled"})


def commit_with_retry(
    db: Session,
    *,
    max_attempts: int = 8,
    base_delay: float = 0.05,
) -> None:
    """Commit with exponential backoff when SQLite reports database is locked."""
    for attempt in range(max_attempts):
        try:
            db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            message = str(getattr(exc, "orig", exc))
            if "database is locked" not in message.lower() or attempt >= max_attempts - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning("SQLite database locked during commit — retrying in %.2fs", delay)
            time.sleep(delay)


def save_pipeline_state(
    db: Session,
    run: FactoryRun,
    *,
    step_outputs: dict[str, str],
    feedback: str,
    step_records: list[dict[str, Any]],
    current_step_id: str | None = None,
    iteration: int = 0,
) -> None:
    run.pipeline_state = {
        "step_outputs": step_outputs,
        "feedback": feedback,
        "step_records": step_records,
        "current_step_id": current_step_id,
        "iteration": iteration,
        # Legacy aliases for older readers
        "draft": step_outputs.get("writer", ""),
        "sources": step_outputs.get("source_finder", ""),
        "fact_check": step_outputs.get("fact_asserter", ""),
    }
    run.review_round = iteration
    db.refresh(run)
    if run.status != "running":
        return
    commit_with_retry(db)


def latest_step_execution(db: Session, run_id: str) -> StepExecution | None:
    return (
        db.query(StepExecution)
        .filter_by(run_id=run_id)
        .order_by(StepExecution.id.desc())
        .first()
    )


def list_step_executions(db: Session, run_id: str) -> list[StepExecution]:
    return (
        db.query(StepExecution)
        .filter_by(run_id=run_id)
        .order_by(StepExecution.id.asc())
        .all()
    )


def reconstruct_pipeline_state(db: Session, run: FactoryRun) -> dict[str, Any] | None:
    """Rebuild pipeline_state from completed step executions when checkpoints were lost."""
    if run.status != "running" or not run.current_step:
        return None

    latest = latest_step_execution(db, run.run_id)
    if latest is None or latest.status != "completed":
        return None

    from article_factory.services.flow_paths import resolve_default_flow_path
    from article_factory.services.flow_storage import read_flow
    from article_factory.services.verdict import Verdict, extract_feedback_body, parse_verdict

    flow_path = (run.flow_path or "").strip() or resolve_default_flow_path(db)
    try:
        flow = read_flow(flow_path)
    except Exception:
        logger.warning("Cannot reconstruct pipeline state — unreadable flow for %s", run.run_id)
        return None

    flow_steps = sorted(flow.steps, key=lambda step: step.order)
    key_to_step = {step.step_key: step for step in flow_steps}
    resume_step = key_to_step.get(run.current_step)
    if resume_step is None:
        return None

    completed = [step for step in list_step_executions(db, run.run_id) if step.status == "completed"]
    if not completed:
        return None

    step_records: list[dict[str, Any]] = []
    step_outputs: dict[str, str] = {}
    for execution in completed:
        content = execution.response_content or ""
        flow_step = key_to_step.get(execution.step_key)
        step_records.append(
            {
                "step_key": execution.step_key,
                "step_name": flow_step.label if flow_step else execution.step_key,
                "content": content,
                "duration_ms": execution.duration_ms,
                "usage": execution.usage or {},
                "tools_used": execution.tools_used or [],
                "turns": execution.turns,
            }
        )
        step_outputs[execution.step_key] = content
        if flow_step is not None:
            step_outputs[flow_step.step_id] = content

    feedback = ""
    iteration = 0
    for execution in completed:
        if execution.step_key != "review" or not execution.response_content:
            continue
        if parse_verdict(execution.response_content) == Verdict.REJECT:
            iteration += 1
            feedback = extract_feedback_body(execution.response_content)

    return {
        "step_outputs": step_outputs,
        "feedback": feedback,
        "step_records": step_records,
        "current_step_id": resume_step.step_id,
        "iteration": iteration,
        "draft": step_outputs.get("writer", ""),
    }


def ensure_run_pipeline_state(db: Session, run: FactoryRun) -> bool:
    """Restore missing pipeline_state so a running flow can resume."""
    if run.pipeline_state:
        return True

    state = reconstruct_pipeline_state(db, run)
    if state is None:
        return False

    run.pipeline_state = state
    iteration = int(state.get("iteration") or 0)
    run.review_round = iteration
    if iteration > 0:
        run.draft_number = max(run.draft_number, iteration + 1)
    commit_with_retry(db)
    logger.info(
        "Reconstructed pipeline state for run %s (resume step=%s, iteration=%s)",
        run.run_id,
        run.current_step,
        iteration,
    )
    return True


def fail_interrupted_run(
    db: Session,
    run: FactoryRun,
    *,
    message: str,
) -> None:
    run.status = "failed"
    run.error = message
    run.finished_at = datetime.now(timezone.utc)
    if run.queue_item_id:
        item = db.get(TopicQueueItem, run.queue_item_id)
        if item and item.status == "running":
            item.status = "failed"
    commit_with_retry(db)
    logger.warning("Marked interrupted run %s as failed: %s", run.run_id, message)


def reconcile_orphaned_runs(db: Session) -> int:
    """Fail running runs that cannot be resumed after a factory restart."""
    running = db.query(FactoryRun).filter_by(status="running").all()
    failed = 0
    for run in running:
        if run.pipeline_state:
            continue
        if ensure_run_pipeline_state(db, run):
            continue
        step = latest_step_execution(db, run.run_id)
        if step is None or step.status == "completed":
            fail_interrupted_run(
                db,
                run,
                message="Run interrupted when the factory restarted — use Retry on the Queue page.",
            )
            failed += 1
            continue
        if step.status in _IN_FLIGHT_STEP_STATUSES or step.status == "failed":
            fail_interrupted_run(
                db,
                run,
                message="Run interrupted when the factory restarted — use Retry on the Queue page.",
            )
            failed += 1
    return failed
