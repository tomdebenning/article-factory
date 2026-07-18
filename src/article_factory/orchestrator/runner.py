from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.cms_client import CmsClient, best_effort_showroom
from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import (
    CompletedArticle,
    FactoryRun,
    FlowQueue,
    ShiftAssignment,
    ShiftDeskSlot,
    ShiftPlan,
    TopicQueueItem,
)
from article_factory.orchestrator.flow_runner import (
    execute_flow_pipeline,
    restore_flow_state,
    sorted_steps,
    step_index_by_id,
)
from article_factory.orchestrator.pipeline import (
    build_manifest,
    new_run_id,
)
from article_factory.services.flow_paths import resolve_default_flow_path
from article_factory.services.flow_storage import read_flow
from article_factory.services.article_text import article_has_content, headline_from_markdown, strip_leading_h1_markdown
from article_factory.services.headline_generator import generate_edition_headline
from article_factory.services.showroom_publish import publish_article_to_showroom
from article_factory.services.token_usage import enrich_manifest, enrich_step_record
from article_factory.services.showroom_status_sync import (
    push_showroom_factory_status,
    refresh_showroom_status,
    schedule_showroom_status_refresh,
)
from article_factory.services.flow_queues import ensure_default_flow_queue
from article_factory.services.shift_dispatch import mark_assignment_status, select_pending_assignments_round_robin
from article_factory.services.persona_selection import assign_reporter_to_assignment, persona_display_name
from article_factory.services.run_provenance import enrich_manifest_with_run_context
from article_factory.services.puller_selection import idle_pullers_for_model
from article_factory.services.run_control import (
    RunCancelledError,
    clear_run_cancel,
    fail_in_flight_steps,
    is_run_cancelled,
    mark_run_cancelled_in_db,
    request_run_cancel,
    take_requeue_flow_path,
)
from article_factory.services.run_recovery import (
    commit_with_retry,
    ensure_run_pipeline_state,
    fail_interrupted_run,
    latest_step_execution,
    reconcile_orphaned_runs,
)
from article_factory.services.runtime_settings import RuntimeSettings, load_runtime_settings
from article_factory.services.flow_versions import resolve_flow_for_run, resolve_flow_version_for_run
from article_factory.services.topic_queue_snapshots import get_or_create_topic_queue_snapshot
from article_factory.services.flow_performance import apply_run_performance
from article_factory.services.showroom_flow_publish import maybe_publish_flow_batch_after_run
from article_factory.services.step_trace import merge_tools_into_manifest, step_executions_payload
from article_factory.services.telemetry import capture_run_telemetry_safe

logger = logging.getLogger(__name__)


def _cms_configured(runtime: RuntimeSettings) -> bool:
    return bool(runtime.cms_url.strip()) and bool(runtime.cms_api_key.strip())



def _mark_dispatch_item(db: Session, run: FactoryRun, status: str) -> None:
    if run.queue_item_id:
        item = db.get(TopicQueueItem, run.queue_item_id)
        if item:
            item.status = status
    mark_assignment_status(
        db,
        assignment_id=run.shift_assignment_id,
        status=status,
        run_id=run.run_id,
    )
    db.commit()


def _mark_queue_item(db: Session, run: FactoryRun, status: str) -> None:
    _mark_dispatch_item(db, run, status)


def _front_queue_priority(db: Session) -> int:
    queued_priorities = [
        row[0]
        for row in db.query(TopicQueueItem.priority)
        .filter_by(status="queued")
        .order_by(TopicQueueItem.priority)
        .limit(1)
        .all()
    ]
    return (queued_priorities[0] - 1) if queued_priorities else 0


def _topic_prompt_for_run(db: Session, run: FactoryRun) -> str:
    stored = (run.topic_prompt or "").strip()
    if stored:
        return stored
    if run.shift_assignment_id:
        assignment = db.get(ShiftAssignment, run.shift_assignment_id)
        if assignment and assignment.prompt.strip():
            return assignment.prompt
    if run.queue_item_id:
        item = db.get(TopicQueueItem, run.queue_item_id)
        if item and item.prompt.strip():
            return item.prompt
    return run.topic_slug.replace("-", " ").title()


def _flow_path_for_run(db: Session, run: FactoryRun) -> str:
    path = (run.flow_path or "").strip()
    if path:
        return path
    return resolve_default_flow_path(db)


async def _emit_run_event(
    cms: CmsClient | None,
    *,
    run_id: str,
    topic_slug: str,
    event: str,
    step_key: str | None = None,
) -> None:
    if cms is None:
        return
    payload: dict = {
        "run_id": run_id,
        "topic_slug": topic_slug,
        "event": event,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if step_key:
        payload["step_key"] = step_key
    await best_effort_showroom(
        f"run event {event} for {run_id}",
        lambda: cms.post_run_event(payload),
    )


async def _complete_run(
    db: Session,
    run: FactoryRun,
    draft: str,
    step_records: list[dict],
    cms: CmsClient | None = None,
) -> None:
    if not article_has_content(draft):
        run.status = "failed"
        run.error = "Flow finished without article content — nothing was published to Showroom"
        run.finished_at = datetime.now(timezone.utc)
        run.pipeline_state = None
        _mark_queue_item(db, run, "failed")
        db.commit()
        capture_run_telemetry_safe(db, run.run_id)
        await maybe_publish_flow_batch_after_run(db, run, cms=cms)
        return

    title_line = headline_from_markdown(draft)
    edition_headline = await generate_edition_headline(db, draft=draft, run=run)
    display_body = strip_leading_h1_markdown(draft)
    enriched_records = [
        enrich_step_record(
            record,
            selected_model=run.selected_model,
            body_markdown=draft,
        )
        for record in step_records
    ]
    manifest = enrich_manifest(
        merge_tools_into_manifest(
            enrich_manifest_with_run_context(
                db,
                run,
                build_manifest(run, enriched_records),
            ),
            step_executions_payload(db, run.run_id),
        ),
        selected_model=run.selected_model,
        body_markdown=display_body,
    )
    apply_run_performance(db, run, enriched_records)
    run.status = "completed"
    run.finished_at = datetime.now(timezone.utc)
    run.manifest = manifest
    run.pipeline_state = None
    db.add(
        CompletedArticle(
            run_id=run.run_id,
            queue_item_id=run.queue_item_id,
            topic_slug=run.topic_slug,
            title=title_line,
            edition_headline=edition_headline,
            summary=display_body[:280],
            body_markdown=display_body,
            manifest=manifest,
        )
    )
    _mark_queue_item(db, run, "completed")
    db.commit()
    capture_run_telemetry_safe(db, run.run_id)

    if cms is not None:
        article = db.query(CompletedArticle).filter_by(run_id=run.run_id).one()
        try:
            await publish_article_to_showroom(db, run=run, article=article, cms=cms)
            run.error = None
            db.commit()
            logger.info("Published run %s to Showroom", run.run_id)
        except Exception as exc:
            logger.warning("Showroom publish failed for %s", run.run_id, exc_info=True)
            run.error = f"Showroom publish failed: {exc}"
            db.commit()
    elif _cms_configured(load_runtime_settings(db)):
        run.error = "Showroom publish skipped: CMS client unavailable"
        db.commit()
    await maybe_publish_flow_batch_after_run(db, run, cms=cms)


async def _execute_pipeline(
    db: Session,
    *,
    run: FactoryRun,
    topic_prompt: str,
    resume_from_step: str | None = None,
) -> FactoryRun:
    flow_path = _flow_path_for_run(db, run)
    runtime = load_runtime_settings(db)
    cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key) if _cms_configured(runtime) else None

    async def emit_step_started(step_key: str) -> None:
        await _emit_run_event(
            cms,
            run_id=run.run_id,
            topic_slug=run.topic_slug,
            event="step_started",
            step_key=step_key,
        )
        schedule_showroom_status_refresh(force=True)

    async def complete_run(draft: str, step_records: list) -> None:
        await _complete_run(db, run, draft, step_records, cms)

    resume_step_id: str | None = None
    if resume_from_step:
        try:
            flow = resolve_flow_for_run(db, run)
            steps = sorted_steps(flow)
            if any(step.step_id == resume_from_step for step in steps):
                resume_step_id = resume_from_step
            else:
                resume_step_id = next(
                    (step.step_id for step in steps if step.step_key == resume_from_step),
                    None,
                )
        except Exception:
            resume_step_id = None

    run_id = run.run_id
    try:
        run = await execute_flow_pipeline(
            db,
            run=run,
            flow_path=flow_path,
            topic_prompt=topic_prompt,
            runtime=runtime,
            cms=cms,
            emit_step_started=emit_step_started,
            complete_run=complete_run,
            resume_from_step_id=resume_step_id,
        )
        db.refresh(run)
        if run.status in ("completed", "failed", "cancelled"):
            capture_run_telemetry_safe(db, run.run_id)
            await maybe_publish_flow_batch_after_run(db, run, cms=cms)
        if run.status == "failed":
            _mark_queue_item(db, run, "failed")
        return run
    except RunCancelledError:
        logger.info("Run %s cancelled", run.run_id)
        db.refresh(run)
        if run.status == "running":
            mark_run_cancelled_in_db(db, run)
        else:
            fail_in_flight_steps(db, run.run_id)
        requeue_flow = await take_requeue_flow_path(run.run_id)
        if requeue_flow and run.queue_item_id:
            item = db.get(TopicQueueItem, run.queue_item_id)
            if item:
                item.status = "queued"
                item.flow_path = requeue_flow
                item.priority = _front_queue_priority(db)
        elif run.queue_item_id:
            _mark_queue_item(db, run, "failed")
        db.commit()
        capture_run_telemetry_safe(db, run.run_id)
        await maybe_publish_flow_batch_after_run(db, run, cms=cms)
        await clear_run_cancel(run.run_id)
        return run
    except asyncio.CancelledError:
        logger.info("Run %s worker task cancelled", run.run_id)
        db.refresh(run)
        if run.status == "running":
            mark_run_cancelled_in_db(db, run)
        else:
            fail_in_flight_steps(db, run.run_id)
            db.commit()
        await clear_run_cancel(run.run_id)
        raise
    except Exception as exc:
        logger.exception("Run %s failed", run_id)
        try:
            db.rollback()
            db.refresh(run)
            run.status = "failed"
            run.error = str(exc) or type(exc).__name__
            run.finished_at = datetime.now(timezone.utc)
            _mark_queue_item(db, run, "failed")
            commit_with_retry(db)
        except Exception:
            logger.exception("Could not persist failure state for run %s", run_id)
        raise


async def schedule_pipeline_for_topic(
    db: Session,
    *,
    topic_slug: str,
    topic_prompt: str,
    queue_item_id: int | None = None,
    selected_puller: str | None = None,
    flow_path: str | None = None,
    flow_version_id: int | None = None,
    shift_plan_id: int | None = None,
    shift_assignment_id: int | None = None,
    reporter_persona_slug: str | None = None,
) -> FactoryRun:
    """Create a run and execute it on the factory loop (returns immediately)."""
    run = await _begin_pipeline_run(
        db,
        topic_slug=topic_slug,
        topic_prompt=topic_prompt,
        queue_item_id=queue_item_id,
        selected_puller=selected_puller,
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        shift_plan_id=shift_plan_id,
        shift_assignment_id=shift_assignment_id,
        reporter_persona_slug=reporter_persona_slug,
    )
    factory_loop.schedule_run(run.run_id, topic_prompt)
    return run


async def _begin_pipeline_run(
    db: Session,
    *,
    topic_slug: str,
    topic_prompt: str,
    queue_item_id: int | None = None,
    selected_puller: str | None = None,
    flow_path: str | None = None,
    flow_version_id: int | None = None,
    shift_plan_id: int | None = None,
    shift_assignment_id: int | None = None,
    reporter_persona_slug: str | None = None,
) -> FactoryRun:
    resolved_flow = (flow_path or "").strip() or resolve_default_flow_path(db)
    first_step = "writer"
    try:
        from article_factory.models import FactoryRun as _FactoryRun

        preview_run = _FactoryRun(flow_path=resolved_flow, flow_version_id=flow_version_id)
        flow = resolve_flow_for_run(db, preview_run)
        steps = sorted_steps(flow)
        if steps:
            first_step = steps[0].step_key
    except Exception:
        pass

    run = FactoryRun(
        run_id=new_run_id(),
        topic_slug=topic_slug,
        topic_prompt=topic_prompt.strip(),
        flow_path=resolved_flow,
        queue_item_id=queue_item_id,
        shift_plan_id=shift_plan_id,
        shift_assignment_id=shift_assignment_id,
        status="running",
        current_step=first_step,
    )
    if selected_puller:
        run.selected_puller = selected_puller

    if shift_plan_id is not None:
        plan = db.get(ShiftPlan, shift_plan_id)
        if plan is not None and (plan.default_model or "").strip():
            run.selected_model = plan.default_model.strip()
    if not (run.selected_model or "").strip():
        runtime = load_runtime_settings(db)
        run.selected_model = (runtime.default_model or "").strip()

    persona_slug = (reporter_persona_slug or "").strip() or None
    if persona_slug:
        run.reporter_persona_slug = persona_slug
        run.reporter_persona_name = persona_display_name(db, persona_slug)

    version = resolve_flow_version_for_run(
        db,
        resolved_flow,
        flow_version_id=flow_version_id,
    )
    run.flow_version_id = version.id
    snapshot = get_or_create_topic_queue_snapshot(
        db,
        flow_queue_id=None,
        queue_item_id=queue_item_id,
    )
    if snapshot:
        run.topic_queue_snapshot_id = snapshot.id

    db.add(run)
    if shift_assignment_id is not None:
        assignment = db.get(ShiftAssignment, shift_assignment_id)
        if assignment is not None:
            assignment.run_id = run.run_id
    db.commit()
    db.refresh(run)

    runtime = load_runtime_settings(db)
    cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key) if _cms_configured(runtime) else None
    await _emit_run_event(cms, run_id=run.run_id, topic_slug=topic_slug, event="run_started")
    schedule_showroom_status_refresh(force=True)
    return run


async def run_pipeline_for_topic(
    db: Session,
    *,
    topic_slug: str,
    topic_prompt: str,
    queue_item_id: int | None = None,
    selected_puller: str | None = None,
    flow_path: str | None = None,
    flow_version_id: int | None = None,
    shift_plan_id: int | None = None,
    shift_assignment_id: int | None = None,
    reporter_persona_slug: str | None = None,
) -> FactoryRun:
    run = await _begin_pipeline_run(
        db,
        topic_slug=topic_slug,
        topic_prompt=topic_prompt,
        queue_item_id=queue_item_id,
        selected_puller=selected_puller,
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        shift_plan_id=shift_plan_id,
        shift_assignment_id=shift_assignment_id,
        reporter_persona_slug=reporter_persona_slug,
    )
    return await _execute_pipeline(db, run=run, topic_prompt=topic_prompt)


async def continue_active_run(db: Session, run: FactoryRun) -> bool:
    """Resume a running pipeline after restart. Returns True if handled."""
    db.refresh(run)
    if run.status != "running":
        return True

    if not run.pipeline_state and not ensure_run_pipeline_state(db, run):
        step = latest_step_execution(db, run.run_id)
        topic_prompt = _topic_prompt_for_run(db, run)
        if step is None:
            logger.info("Restarting run %s — pipeline never reached the control plane", run.run_id)
            await _execute_pipeline(db, run=run, topic_prompt=topic_prompt)
            return True
        if step.status in {"pending", "submitted", "waiting", "pulled", "failed"}:
            resume_step = run.current_step or step.step_key
            logger.info(
                "Resuming in-flight run %s from step %s (execution status=%s)",
                run.run_id,
                resume_step,
                step.status,
            )
            await _execute_pipeline(
                db,
                run=run,
                topic_prompt=topic_prompt,
                resume_from_step=resume_step,
            )
            return True
        fail_interrupted_run(
            db,
            run,
            message="Run interrupted when the factory restarted — use Retry on the Queue page.",
        )
        return True

    topic_prompt = _topic_prompt_for_run(db, run)
    resume_step = run.current_step
    if not resume_step:
        try:
            flow = resolve_flow_for_run(db, run)
            steps = sorted_steps(flow)
            resume_step = steps[0].step_key if steps else None
        except Exception:
            resume_step = None
    logger.info("Resuming run %s from step %s", run.run_id, resume_step)
    await _execute_pipeline(db, run=run, topic_prompt=topic_prompt, resume_from_step=resume_step)
    return True


class FactoryLoop:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._run_workers: dict[str, asyncio.Task] = {}
        self._reserved_pullers: set[str] = set()
        self._dispatch_event: asyncio.Event | None = None
        self._shift_rr_index: int = 0

    def _ensure_dispatch_event(self) -> asyncio.Event:
        if self._dispatch_event is None:
            self._dispatch_event = asyncio.Event()
        return self._dispatch_event

    @property
    def active_worker_count(self) -> int:
        return len(self._run_workers)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._task is not None and self._task.done():
            self._task = None
        from article_factory.db import SessionLocal

        db = SessionLocal()
        try:
            reconcile_orphaned_runs(db)
        finally:
            db.close()
        self._running = True
        self._dispatch_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    def request_dispatch(self) -> None:
        """Wake the dispatch loop so queued topics start without waiting for the poll interval."""
        if self._running:
            self._ensure_dispatch_event().set()

    async def stop(self) -> None:
        self._running = False
        for worker in list(self._run_workers.values()):
            worker.cancel()
        self._run_workers.clear()
        self._reserved_pullers.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def cancel_run_workers(self, *, run_ids: list[str], queue_item_ids: list[int]) -> int:
        """Cancel asyncio tasks driving the given runs (hard stop)."""
        keys: set[str] = {f"run-{run_id}" for run_id in run_ids}
        keys.update(f"queue-{item_id}" for item_id in queue_item_ids)
        cancelled = 0
        for key in keys:
            task = self._run_workers.pop(key, None)
            if task is None or task.done():
                continue
            task.cancel()
            cancelled += 1
        self._reserved_pullers.clear()
        return cancelled

    def clear_reserved_pullers(self) -> None:
        self._reserved_pullers.clear()

    def _prune_stale_puller_reservations(self) -> None:
        active_queue_workers = [
            key
            for key, task in self._run_workers.items()
            if key.startswith("queue-") and not task.done()
        ]
        if not active_queue_workers:
            self._reserved_pullers.clear()

    async def ensure_running(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._task is not None and self._task.done():
            logger.warning("Factory dispatch loop stopped unexpectedly — restarting")
            self._task = None
        await self.start()

    def _spawn_worker(self, key: str, coro) -> None:
        if key in self._run_workers:
            return

        async def wrapped() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Run worker failed for %s", key)

        task = asyncio.create_task(wrapped())
        self._run_workers[key] = task
        task.add_done_callback(lambda _t: self._run_workers.pop(key, None))

    def schedule_run(self, run_id: str, topic_prompt: str) -> None:
        """Execute a newly created run on the factory loop without blocking the caller."""
        worker_key = f"run-{run_id}"
        if worker_key in self._run_workers:
            return
        self._spawn_worker(worker_key, self._execute_scheduled_run(run_id, topic_prompt))
        self.request_dispatch()

    async def _execute_scheduled_run(self, run_id: str, topic_prompt: str) -> None:
        from article_factory.db import SessionLocal

        db = SessionLocal()
        try:
            run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
            if run is None or run.status != "running":
                return
            await _execute_pipeline(db, run=run, topic_prompt=topic_prompt)
        finally:
            db.close()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._dispatch_tick()
            except Exception:
                logger.exception("Factory loop error")
            await self._wait_for_next_tick()

    async def _wait_for_next_tick(self) -> None:
        event = self._ensure_dispatch_event()
        if not event.is_set():
            try:
                await asyncio.wait_for(
                    event.wait(),
                    timeout=settings.dispatch_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
        event.clear()

    async def _dispatch_tick(self) -> None:
        from article_factory.db import SessionLocal
        from article_factory.services.shift_boundary_scheduler import process_shift_boundaries
        from article_factory.services.shift_t15_scheduler import process_t15_due_plans
        from article_factory.services.showroom_status_sync import schedule_showroom_status_refresh

        db = SessionLocal()
        boundary_summary: dict[str, int] = {"ended": 0, "activated": 0, "alerts": 0}
        try:
            processed = await process_t15_due_plans(db)
            if processed:
                db.commit()
        except Exception:
            logger.exception("T-15 scheduler error")
            db.rollback()
        finally:
            db.close()

        db = SessionLocal()
        try:
            boundary_summary = await process_shift_boundaries(db)
            if any(boundary_summary.values()):
                db.commit()
                if boundary_summary.get("activated"):
                    self.request_dispatch()
                schedule_showroom_status_refresh(force=True)
        except Exception:
            logger.exception("Shift boundary scheduler error")
            db.rollback()
        finally:
            db.close()

        self._prune_stale_puller_reservations()

        model = ""
        cp_url = ""
        active_plan = None
        queued_count_hint = 0

        db = SessionLocal()
        try:
            ensure_default_flow_queue(db)
            running_runs = (
                db.query(FactoryRun)
                .filter(FactoryRun.status == "running")
                .order_by(FactoryRun.started_at.asc())
                .all()
            )

            for run in running_runs:
                if await is_run_cancelled(run.run_id):
                    continue
                worker_key = f"run-{run.run_id}"
                if worker_key in self._run_workers:
                    continue
                if run.shift_assignment_id is not None:
                    assignment_worker_key = f"assignment-{run.shift_assignment_id}"
                    if assignment_worker_key in self._run_workers:
                        continue
                if run.queue_item_id is not None:
                    queue_worker_key = f"queue-{run.queue_item_id}"
                    if queue_worker_key in self._run_workers:
                        continue
                self._spawn_worker(worker_key, self._continue_run(run.run_id))

            runtime = load_runtime_settings(db)
            active_plan = (
                db.query(ShiftPlan)
                .filter_by(status="active")
                .order_by(ShiftPlan.activated_at.desc())
                .first()
            )
            model = runtime.default_model.strip()
            if active_plan and (active_plan.default_model or "").strip():
                model = active_plan.default_model.strip()
            cp_url = runtime.control_plane_url.strip()
            in_use = {r.selected_puller for r in running_runs if r.selected_puller}
            in_use |= self._reserved_pullers
            queued_count_hint = db.query(ShiftAssignment).filter_by(status="pending").count()
        finally:
            db.close()

        if not model or not cp_url or active_plan is None:
            return

        cp = ControlPlaneClient(base_url=cp_url)
        try:
            pullers = await cp.list_pullers(active_only=False)
        except Exception:
            logger.warning("Could not list pullers for dispatch")
            return

        idle = idle_pullers_for_model(pullers, model, exclude=in_use)
        if not idle:
            queued_count = queued_count_hint
            if queued_count and self._reserved_pullers:
                logger.warning(
                    "Queue has %s topic(s) but no idle pullers — clearing stale reservations: %s",
                    queued_count,
                    sorted(self._reserved_pullers),
                )
                self._reserved_pullers.clear()
                idle = idle_pullers_for_model(pullers, model, exclude=in_use)
            if not idle:
                if queued_count:
                    logger.warning(
                        "Queue has %s topic(s) but no idle pullers for model %s",
                        queued_count,
                        model,
                    )
                return

        db = SessionLocal()
        try:
            picked, next_index = select_pending_assignments_round_robin(
                db,
                limit=len(idle),
                start_index=self._shift_rr_index,
            )
            self._shift_rr_index = next_index

            for (assignment, desk, plan), puller in zip(picked, idle, strict=False):
                worker_key = f"assignment-{assignment.id}"
                if worker_key in self._run_workers:
                    continue
                puller_name = str(puller.get("puller_name") or "")
                if not puller_name:
                    continue
                persona_slug = assign_reporter_to_assignment(
                    db,
                    assignment=assignment,
                    desk_slot=desk,
                    shift_plan_id=plan.id,
                )
                assignment.status = "running"
                commit_with_retry(db)
                self._reserved_pullers.add(puller_name)
                self._spawn_worker(
                    worker_key,
                    self._run_shift_assignment(
                        assignment.id,
                        desk.topic_slug,
                        assignment.prompt,
                        desk.desk_path,
                        puller_name,
                        desk.flow_version_id,
                        plan.id,
                        persona_slug,
                    ),
                )
            if picked:
                schedule_showroom_status_refresh(force=True)
        finally:
            db.close()

    async def _continue_run(self, run_id: str) -> None:
        from article_factory.db import SessionLocal

        db = SessionLocal()
        try:
            run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
            if run is None:
                return
            await continue_active_run(db, run)
        finally:
            db.close()

    async def _run_shift_assignment(
        self,
        assignment_id: int,
        topic_slug: str,
        topic_prompt: str,
        flow_path: str,
        puller_name: str,
        flow_version_id: int | None = None,
        shift_plan_id: int | None = None,
        reporter_persona_slug: str | None = None,
    ) -> None:
        from article_factory.db import SessionLocal

        try:
            db = SessionLocal()
            try:
                await run_pipeline_for_topic(
                    db,
                    topic_slug=topic_slug,
                    topic_prompt=topic_prompt,
                    selected_puller=puller_name,
                    flow_path=flow_path,
                    flow_version_id=flow_version_id,
                    shift_plan_id=shift_plan_id,
                    shift_assignment_id=assignment_id,
                    reporter_persona_slug=reporter_persona_slug,
                )
            finally:
                db.close()
        finally:
            self._reserved_pullers.discard(puller_name)

    async def _run_queue_item(
        self,
        item_id: int,
        topic_slug: str,
        topic_prompt: str,
        flow_path: str,
        puller_name: str,
        flow_version_id: int | None = None,
    ) -> None:
        from article_factory.db import SessionLocal

        try:
            db = SessionLocal()
            try:
                await run_pipeline_for_topic(
                    db,
                    topic_slug=topic_slug,
                    topic_prompt=topic_prompt,
                    queue_item_id=item_id,
                    selected_puller=puller_name,
                    flow_path=flow_path,
                    flow_version_id=flow_version_id,
                )
            finally:
                db.close()
        finally:
            self._reserved_pullers.discard(puller_name)


factory_loop = FactoryLoop()
