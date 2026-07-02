from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncio

import pytest

import article_factory.db as db_module
from article_factory.cms_client import CmsRequestError
from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem
from article_factory.orchestrator.runner import FactoryLoop, run_pipeline_for_topic
from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_storage import write_flow


def _step_record(step_key: str, content: str) -> dict:
    return {
        "step_key": step_key,
        "step_name": step_key,
        "content": content,
        "duration_ms": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "completed_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def pipeline_env(configured_db, monkeypatch) -> None:
    async def fake_select(_cp, model: str) -> str:
        return "auto-puller"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"default_model": "test-model"})
    finally:
        db.close()


@pytest.mark.asyncio
async def test_fact_asserter_receives_writer_draft(configured_db, pipeline_env, monkeypatch) -> None:
    seen: dict[str, dict] = {}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        seen[ctx.step_key] = dict(ctx.variables)
        if ctx.step_key == "writer":
            return _step_record("writer", "# Big Win\n\nGreat game.")
        if ctx.step_key == "review":
            return _step_record("review", "Looks good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "ok")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: AsyncMock())
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    db = db_module.SessionLocal()
    try:
        run = await run_pipeline_for_topic(db, topic_slug="sports", topic_prompt="Cover the game")
        assert run.status == "completed"
        assert "Big Win" in seen["fact_asserter"]["draft"]
        assert "Great game" in seen["fact_asserter"]["draft"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_publishes_on_accept(configured_db, pipeline_env, monkeypatch) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()
    cms.post_run_complete = AsyncMock(return_value={"ok": True})

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Big Win\n\nGreat game.")
        if ctx.step_key == "source_finder":
            return _step_record("source_finder", "https://example.com")
        if ctx.step_key == "fact_asserter":
            return _step_record("fact_asserter", "Verified")
        if ctx.step_key == "review":
            return _step_record("review", "Good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
            },
        )
        run = await run_pipeline_for_topic(db, topic_slug="sports", topic_prompt="Cover the game")
        assert run.status == "completed"
        assert run.selected_puller == "auto-puller"
        assert run.selected_model == "test-model"
        cms.post_run_complete.assert_called_once()
        from article_factory.models import CompletedArticle

        article = db.query(CompletedArticle).filter_by(run_id=run.run_id).one()
        assert "Big Win" in article.body_markdown
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_keeps_completed_when_showroom_publish_fails(
    configured_db, pipeline_env, monkeypatch
) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()
    cms.post_run_complete = AsyncMock(side_effect=CmsRequestError("Showroom CMS: Unknown topic general"))

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Big Win\n\nGreat game.")
        if ctx.step_key == "source_finder":
            return _step_record("source_finder", "https://example.com")
        if ctx.step_key == "fact_asserter":
            return _step_record("fact_asserter", "Verified")
        if ctx.step_key == "review":
            return _step_record("review", "Good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
            },
        )
        run = await run_pipeline_for_topic(db, topic_slug="general", topic_prompt="Cover the game")
        assert run.status == "completed"
        assert "Showroom publish failed" in (run.error or "")
        assert "Unknown topic general" in (run.error or "")
        article = db.query(CompletedArticle).filter_by(run_id=run.run_id).one()
        assert "Big Win" in article.body_markdown
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_reject_then_accept(configured_db, pipeline_env, monkeypatch) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()
    cms.post_run_complete = AsyncMock(return_value={})
    review_calls = {"n": 0}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody")
        if ctx.step_key in ("source_finder", "fact_asserter"):
            return _step_record(ctx.step_key, "ok")
        if ctx.step_key == "review":
            review_calls["n"] += 1
            if review_calls["n"] == 1:
                return _step_record("review", "Fix the lede.\n\nVERDICT: REJECT")
            return _step_record("review", "All set.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    db = db_module.SessionLocal()
    try:
        run = await run_pipeline_for_topic(db, topic_slug="sports", topic_prompt="Topic")
        assert run.status == "completed"
        assert run.review_round >= 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_max_review_rounds(configured_db, pipeline_env, monkeypatch) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "Still bad.\n\nVERDICT: REJECT")
        return _step_record(ctx.step_key, "content")

    from article_factory.services.flow_storage import read_flow, write_flow

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    db = db_module.SessionLocal()
    try:
        flow = read_flow("sports/standard-4-step.flow.json")
        flow.max_iterations = 1
        write_flow("sports/standard-4-step.flow.json", flow)
        run = await run_pipeline_for_topic(db, topic_slug="sports", topic_prompt="Topic")
        assert run.status == "failed"
        assert run.error == "Max flow iterations exceeded"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_with_queue_item(configured_db, pipeline_env, monkeypatch) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()
    cms.post_run_complete = AsyncMock(return_value={})

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "Looks good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "# Title\n\nBody")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Queued topic", status="running")
        db.add(item)
        db.commit()
        run = await run_pipeline_for_topic(
            db,
            topic_slug="sports",
            topic_prompt="Queued topic",
            queue_item_id=item.id,
        )
        assert run.status == "completed"
        updated = db.get(TopicQueueItem, item.id)
        assert updated.status == "completed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_pipeline_exception(configured_db, pipeline_env, monkeypatch) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()

    async def boom(ctx, cp=None, tracer=None, run_id=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", boom)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    db = db_module.SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await run_pipeline_for_topic(db, topic_slug="sports", topic_prompt="Topic")
        run = db.query(FactoryRun).order_by(FactoryRun.id.desc()).first()
        assert run.status == "failed"
        assert run.error == "boom"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_flow_two_steps_completes(configured_db, pipeline_env, monkeypatch) -> None:
    from article_factory.services.flow_defaults import build_writer_review_flow
    from article_factory.services.flow_storage import write_flow

    write_flow("test/writer-review.flow.json", build_writer_review_flow())

    cms = AsyncMock()
    cms.post_run_event = AsyncMock()
    cms.post_run_complete = AsyncMock(return_value={})

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "Looks good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "# Title\n\nBody")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    db = db_module.SessionLocal()
    try:
        run = await run_pipeline_for_topic(
            db,
            topic_slug="sports",
            topic_prompt="Topic",
            flow_path="test/writer-review.flow.json",
        )
        assert run.status == "completed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_waits_on_active_run(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    hold = asyncio.Event()

    async def slow_continue(self, run_id: str) -> None:
        await hold.wait()

    monkeypatch.setattr(FactoryLoop, "_continue_run", slow_continue)

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="active-run",
                topic_slug="sports",
                status="running",
                current_step="writer",
                pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
            )
        )
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    assert "run-active-run" in loop._run_workers
    hold.set()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_factory_loop_stop_without_start() -> None:
    loop = FactoryLoop()
    await loop.stop()
    assert loop._running is False


@pytest.mark.asyncio
async def test_factory_loop_start_stop(monkeypatch) -> None:
    loop = FactoryLoop()

    async def noop_loop() -> None:
        while loop._running:
            await asyncio.sleep(0)

    monkeypatch.setattr(loop, "_loop", noop_loop)
    await loop.start()
    assert loop._running is True
    await loop.start()
    await loop.stop()
    assert loop._running is False
    assert loop._task is None


@pytest.mark.asyncio
async def test_run_pipeline_with_cms_events(configured_db, pipeline_env, monkeypatch) -> None:
    cms = AsyncMock()
    cms.post_run_event = AsyncMock()

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "review":
            return _step_record("review", "Looks good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "# Title\n\nBody")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: cms)

    import article_factory.db as db_module
    from article_factory.models import FactorySettings
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "key",
            },
        )
        run = await run_pipeline_for_topic(db, topic_slug="sports", topic_prompt="Topic")
        assert run.status == "completed"
        assert cms.post_run_event.await_count >= 2
    finally:
        db.close()


@pytest.mark.asyncio
async def test_factory_loop_processes_queue(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    started: list[str | None] = []

    async def fake_pipeline(db, *, topic_slug, topic_prompt, queue_item_id=None, selected_puller=None, flow_path=None):
        started.append(selected_puller)
        run = FactoryRun(run_id=f"run-{queue_item_id}", topic_slug=topic_slug, status="completed")
        db.add(run)
        db.commit()
        return run

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "puller-1",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            }
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", fake_pipeline)

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="From queue"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert started == ["puller-1"]


@pytest.mark.asyncio
async def test_factory_loop_handles_errors(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "puller-1",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            }
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.run_pipeline_for_topic",
        AsyncMock(side_effect=RuntimeError("pipeline fail")),
    )

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Will fail"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert loop.active_worker_count == 0


@pytest.mark.asyncio
async def test_factory_loop_idle_waits(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True

    async def stop_after_wait(self) -> None:
        loop._running = False

    monkeypatch.setattr(FactoryLoop, "_wait_for_next_tick", stop_after_wait)
    await loop._loop()


@pytest.mark.asyncio
async def test_factory_loop_parallel_dispatch(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    started: list[str | None] = []

    async def fake_pipeline(db, *, topic_slug, topic_prompt, queue_item_id=None, selected_puller=None, flow_path=None):
        started.append(selected_puller)
        run = FactoryRun(
            run_id=f"run-{queue_item_id}",
            topic_slug=topic_slug,
            status="running",
            selected_puller=selected_puller,
        )
        db.add(run)
        db.commit()
        return run

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "puller-a",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            },
            {
                "puller_name": "puller-b",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            },
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)
    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", fake_pipeline)

    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="First"))
        db.add(TopicQueueItem(topic_slug="sports", prompt="Second"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert sorted(started) == ["puller-a", "puller-b"]


@pytest.mark.asyncio
async def test_wait_for_next_tick_wakes_on_request(monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True
    loop._dispatch_event = asyncio.Event()
    monkeypatch.setattr("article_factory.orchestrator.runner.settings.dispatch_interval_seconds", 30)

    task = asyncio.create_task(loop._wait_for_next_tick())
    await asyncio.sleep(0.01)
    loop.request_dispatch()
    await asyncio.wait_for(task, timeout=0.5)


@pytest.mark.asyncio
async def test_request_dispatch_wakes_loop(monkeypatch) -> None:
    loop = FactoryLoop()
    monkeypatch.setattr("article_factory.orchestrator.runner.settings.dispatch_interval_seconds", 30)

    wait_started = asyncio.Event()
    release_wait = asyncio.Event()

    async def fake_wait(self) -> None:
        wait_started.set()
        await release_wait.wait()

    ticks = 0

    async def count_tick(self) -> None:
        nonlocal ticks
        ticks += 1
        if ticks >= 2:
            self._running = False

    loop._running = True
    monkeypatch.setattr(FactoryLoop, "_wait_for_next_tick", fake_wait)
    monkeypatch.setattr(FactoryLoop, "_dispatch_tick", count_tick)

    task = asyncio.create_task(loop._loop())
    await wait_started.wait()
    assert ticks == 1
    loop.request_dispatch()
    release_wait.set()
    await asyncio.wait_for(task, timeout=1)
    assert ticks >= 2


def test_prune_stale_puller_reservations() -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("puller-a")
    loop._prune_stale_puller_reservations()
    assert loop._reserved_pullers == set()


@pytest.mark.asyncio
async def test_dispatch_does_not_resume_fresh_queue_run(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    continued: list[str] = []

    async def track_continue(self, run_id: str) -> None:
        continued.append(run_id)

    monkeypatch.setattr(FactoryLoop, "_continue_run", track_continue)
    monkeypatch.setattr(
        "article_factory.orchestrator.runner.idle_pullers_for_model",
        lambda pullers, model, exclude=None: [],
    )

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Fresh", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-fresh",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
            )
        )
        db.commit()
        loop._run_workers[f"queue-{item.id}"] = asyncio.create_task(asyncio.sleep(60))
    finally:
        db.close()

    await loop._dispatch_tick()
    loop._run_workers[f"queue-{item.id}"].cancel()
    assert continued == []


@pytest.mark.asyncio
async def test_trigger_run_endpoint(client, api_headers, configured_db, pipeline_env, monkeypatch) -> None:
    async def fake_run(db, *, topic_slug, topic_prompt, queue_item_id=None, flow_path=None):
        run = FactoryRun(run_id="run-trigger", topic_slug=topic_slug, status="published")
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    monkeypatch.setattr("article_factory.routes.admin.run_pipeline_for_topic", fake_run)
    response = client.post(
        "/api/runs/trigger",
        headers=api_headers,
        json={"topic_slug": "sports", "prompt": "Manual run"},
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == "run-trigger"


@pytest.mark.asyncio
async def test_run_pipeline_fails_when_flow_completes_without_content(
    configured_db, pipeline_env, monkeypatch
) -> None:
    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return _step_record(ctx.step_key, "")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: AsyncMock())
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    write_flow(
        "test/SimpleTest.flow.json",
        new_flow_definition(slug="SimpleTest", display_name="SimpleTest", step_count=1),
    )

    db = db_module.SessionLocal()
    try:
        run = await run_pipeline_for_topic(
            db,
            topic_slug="general",
            topic_prompt="Write something",
            flow_path="test/SimpleTest.flow.json",
        )
        assert run.status == "failed"
        assert "without article content" in (run.error or "")
        assert db.query(CompletedArticle).filter_by(run_id=run.run_id).count() == 0
    finally:
        db.close()
