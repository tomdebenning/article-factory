"""Background runner for prompt improvement jobs."""

from __future__ import annotations

import asyncio
import logging

from article_factory.db import SessionLocal
from article_factory.models import PromptImprovementJob
from article_factory.services.prompt_improvement import run_prompt_improvement_job
from article_factory.services.runtime_settings import load_runtime_settings

logger = logging.getLogger(__name__)


class PromptImprovementRunner:
    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        db = SessionLocal()
        try:
            stale = (
                db.query(PromptImprovementJob)
                .filter(PromptImprovementJob.status.in_(["queued", "running"]))
                .all()
            )
            for job in stale:
                if job.status == "running":
                    job.status = "failed"
                    job.error_message = "Interrupted by factory restart"
                elif job.status == "queued":
                    asyncio.create_task(self._run_job(job.id))
            db.commit()
        finally:
            db.close()

    def enqueue(self, job_id: int) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run_job(job_id))

    async def _run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            runtime = load_runtime_settings(db)
            await run_prompt_improvement_job(
                db,
                job_id,
                control_plane_url=runtime.control_plane_url,
            )
        except Exception:
            logger.exception("Prompt improvement job %s crashed", job_id)
            job = db.get(PromptImprovementJob, job_id)
            if job and job.status not in {"completed", "failed"}:
                job.status = "failed"
                job.error_message = "Unexpected error running prompt improvement job"
                db.commit()
        finally:
            db.close()
            self._tasks.pop(job_id, None)


prompt_improvement_runner = PromptImprovementRunner()
