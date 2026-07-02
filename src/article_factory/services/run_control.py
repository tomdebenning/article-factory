from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, StepExecution, TopicQueueItem

_cancelled_run_ids: set[str] = set()
_requeue_after_cancel: dict[str, str] = {}
_lock = asyncio.Lock()

_IN_FLIGHT_STEP_STATUSES = frozenset({"pending", "submitted", "waiting", "pulled"})


class RunCancelledError(Exception):
    """Raised when a run is stopped from the admin UI."""


async def request_run_cancel(run_id: str, *, requeue_flow_path: str | None = None) -> None:
    async with _lock:
        _cancelled_run_ids.add(run_id)
        if requeue_flow_path:
            _requeue_after_cancel[run_id] = requeue_flow_path.strip()
        else:
            _requeue_after_cancel.pop(run_id, None)


async def is_run_cancelled(run_id: str) -> bool:
    async with _lock:
        return run_id in _cancelled_run_ids


async def clear_run_cancel(run_id: str) -> None:
    async with _lock:
        _cancelled_run_ids.discard(run_id)
        _requeue_after_cancel.pop(run_id, None)


async def take_requeue_flow_path(run_id: str) -> str | None:
    async with _lock:
        return _requeue_after_cancel.pop(run_id, None)


def fail_in_flight_steps(db: Session, run_id: str, *, error: str = "Run stopped") -> int:
    now = datetime.now(timezone.utc)
    steps = (
        db.query(StepExecution)
        .filter_by(run_id=run_id)
        .filter(StepExecution.status.in_(_IN_FLIGHT_STEP_STATUSES))
        .all()
    )
    for step in steps:
        step.status = "failed"
        step.error = error
        step.completed_at = now
    return len(steps)


def mark_run_cancelled_in_db(
    db: Session,
    run: FactoryRun,
    *,
    error: str = "Run stopped",
    queue_item_status: str = "failed",
) -> None:
    now = datetime.now(timezone.utc)
    run.status = "cancelled"
    run.error = error
    run.finished_at = now
    run.pipeline_state = None
    fail_in_flight_steps(db, run.run_id, error=error)
    if run.queue_item_id:
        item = db.get(TopicQueueItem, run.queue_item_id)
        if item and item.status == "running":
            item.status = queue_item_status


def reconcile_stale_running_queue_items(db: Session) -> int:
    """Mark queue items stuck as running when no factory run is active for them."""
    fixed = 0
    running_items = db.query(TopicQueueItem).filter_by(status="running").all()
    for item in running_items:
        active = (
            db.query(FactoryRun)
            .filter_by(queue_item_id=item.id, status="running")
            .one_or_none()
        )
        if active is None:
            item.status = "failed"
            fixed += 1
    return fixed


def reassert_runs_stopped(db: Session, run_ids: list[str], *, error: str = "Run stopped") -> int:
    """Idempotently ensure requested runs are not left in running state."""
    updated = 0
    for run_id in run_ids:
        run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
        if run is None or run.status != "running":
            continue
        mark_run_cancelled_in_db(db, run, error=error)
        updated += 1
    return updated


async def ensure_run_active(db: Session, run: FactoryRun) -> None:
    """Refresh run state and abort if the user stopped this run."""
    db.refresh(run)
    if run.status != "running":
        raise RunCancelledError(f"Run {run.run_id} was stopped")
    if await is_run_cancelled(run.run_id):
        raise RunCancelledError(f"Run {run.run_id} was stopped")

