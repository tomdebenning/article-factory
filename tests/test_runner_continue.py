from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import article_factory.db as db_module
from article_factory.models import FactoryRun, StepExecution, TopicQueueItem
from article_factory.orchestrator.runner import (
    FactoryLoop,
    _complete_run,
    _front_queue_priority,
    _topic_prompt_for_run,
    continue_active_run,
)


@pytest.mark.asyncio
async def test_continue_active_run_non_running_returns_true(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="run-done", topic_slug="sports", status="completed")
        db.add(run)
        db.commit()
        assert await continue_active_run(db, run) is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_fails_interrupted(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-interrupted",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id=run.run_id,
                step_key="writer",
                status="completed",
                puller="puller-1",
                model="test-model",
            )
        )
        db.commit()

        monkeypatch.setattr(
            "article_factory.orchestrator.runner.ensure_run_pipeline_state",
            lambda _db, _run: False,
        )
        executed = {"called": False}

        async def fake_execute(*args, **kwargs):
            executed["called"] = True

        monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)

        result = await continue_active_run(db, run)
        assert result is True
        assert executed["called"] is False
        db.refresh(run)
        assert run.status == "failed"
        assert "Retry" in (run.error or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_resumes_pipeline(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-resume",
            topic_slug="sports",
            status="running",
            current_step="writer",
            pipeline_state={"draft": "# T\n\nB", "step_records": [], "step_outputs": {}},
        )
        db.add(run)
        db.commit()

        async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
            run.status = "completed"
            db.commit()
            return run

        monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)
        result = await continue_active_run(db, run)
        assert result is True
        db.refresh(run)
        assert run.status == "completed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_infers_first_step(configured_db, monkeypatch) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-infer",
            topic_slug="sports",
            status="running",
            flow_path="sports/standard-4-step.flow.json",
            pipeline_state={"draft": "", "step_records": [], "step_outputs": {}},
        )
        db.add(run)
        db.commit()

        captured: dict = {}

        async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
            captured["resume_from_step"] = resume_from_step
            run.status = "completed"
            db.commit()
            return run

        monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)
        await continue_active_run(db, run)
        assert captured["resume_from_step"] == "writer"
    finally:
        db.close()


def test_front_queue_priority(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        assert _front_queue_priority(db) == 0
        db.add(TopicQueueItem(topic_slug="sports", prompt="A", status="queued", priority=10))
        db.add(TopicQueueItem(topic_slug="sports", prompt="B", status="queued", priority=5))
        db.commit()
        assert _front_queue_priority(db) == 4
    finally:
        db.close()


def test_topic_prompt_for_run(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="  Custom prompt  ", status="queued")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-prompt",
            topic_slug="big-game",
            queue_item_id=item.id,
            status="running",
        )
        db.add(run)
        db.commit()
        assert _topic_prompt_for_run(db, run) == "  Custom prompt  "

        run2 = FactoryRun(run_id="run-prompt-2", topic_slug="big-game", status="running")
        db.add(run2)
        db.commit()
        assert _topic_prompt_for_run(db, run2) == "Big Game"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_complete_run_empty_draft_marks_failed(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-empty",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
        )
        db.add(run)
        db.commit()

        await _complete_run(db, run, "", [], cms=None)
        db.refresh(run)
        db.refresh(item)
        assert run.status == "failed"
        assert "without article content" in (run.error or "")
        assert item.status == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_complete_run_cms_configured_but_unavailable(configured_db, monkeypatch) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
        run = FactoryRun(
            run_id="run-skip-publish",
            topic_slug="sports",
            status="running",
            selected_model="m1",
        )
        db.add(run)
        db.commit()

        await _complete_run(
            db,
            run,
            "# Title\n\nBody content here.",
            [{"step_key": "writer", "content": "Body"}],
            cms=None,
        )
        db.refresh(run)
        assert run.status == "completed"
        assert "Showroom publish skipped" in (run.error or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_cancel_run_workers() -> None:
    import asyncio

    loop = FactoryLoop()
    task = asyncio.create_task(asyncio.sleep(60))
    loop._run_workers["run-active"] = task
    loop._reserved_pullers.add("puller-a")

    cancelled = loop.cancel_run_workers(run_ids=["active"], queue_item_ids=[99])
    assert cancelled == 1
    assert loop._reserved_pullers == set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_factory_loop_ensure_running_restarts(monkeypatch) -> None:
    import asyncio

    loop = FactoryLoop()
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    loop._task = done_task

    started = {"n": 0}

    async def fake_start(self):
        started["n"] += 1
        self._running = True
        self._task = asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(FactoryLoop, "start", fake_start)
    await loop.ensure_running()
    assert started["n"] == 1
