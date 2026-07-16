from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncio

import pytest

import article_factory.db as db_module
from article_factory.cms_client import CmsRequestError
from article_factory.models import CompletedArticle, FactoryRun, TopicQueueItem
from article_factory.orchestrator.runner import FactoryLoop, continue_active_run, run_pipeline_for_topic
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

    async def fake_pipeline(
        db,
        *,
        topic_slug,
        topic_prompt,
        queue_item_id=None,
        selected_puller=None,
        flow_path=None,
        flow_version_id=None,
    ):
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

    async def fake_pipeline(
        db,
        *,
        topic_slug,
        topic_prompt,
        queue_item_id=None,
        selected_puller=None,
        flow_path=None,
        flow_version_id=None,
    ):
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


@pytest.mark.asyncio
async def test_run_pipeline_continues_when_showroom_run_event_fails(
    configured_db,
    pipeline_env,
    monkeypatch,
) -> None:
    import httpx

    cms = AsyncMock()
    cms.post_run_event = AsyncMock(side_effect=httpx.ConnectError("Showroom down"))
    cms.post_run_complete = AsyncMock(return_value={"ok": True})

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "writer":
            return _step_record("writer", "# Title\n\nBody.")
        if ctx.step_key == "review":
            return _step_record("review", "Good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "ok")

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
        cms.post_run_event.assert_awaited()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_custom_step_keys_pass_draft_to_review(configured_db, pipeline_env, monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        if ctx.step_key == "step_2":
            seen["draft"] = ctx.variables.get("draft", "")
        if ctx.step_key == "step_1":
            return _step_record("step_1", "Custom essay body for review.")
        if ctx.step_key == "step_2":
            assert "Custom essay body" in ctx.variables.get("draft", "")
            return _step_record("step_2", "Good.\n\nVERDICT: ACCEPT")
        return _step_record(ctx.step_key, "ok")

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)
    monkeypatch.setattr("article_factory.orchestrator.runner.CmsClient", lambda **kwargs: AsyncMock())
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: AsyncMock())

    from article_factory.services.flow_schema import FlowDefinition, FlowStep, FlowStepCompletion
    from article_factory.services.flow_storage import write_flow

    flow = FlowDefinition(
        slug="custom-step-keys",
        display_name="Custom",
        article_step_id="essay-id",
        steps=[
            FlowStep(
                step_id="essay-id",
                order=1,
                step_key="step_1",
                label="Essayist",
                user_prompt_template="{{topic}}",
            ),
            FlowStep(
                step_id="editor-id",
                order=2,
                step_key="step_2",
                label="Editor",
                user_prompt_template="Draft:\n{{draft}}\n\nReview and VERDICT: ACCEPT or REJECT.",
                completion=FlowStepCompletion(
                    can_complete=True,
                    can_loop=True,
                    loop_goto_step_id="essay-id",
                ),
            ),
        ],
    )
    write_flow("test/custom-step-keys.flow.json", flow)

    db = db_module.SessionLocal()
    try:
        run = await run_pipeline_for_topic(
            db,
            topic_slug="sports",
            topic_prompt="Topic here",
            flow_path="test/custom-step-keys.flow.json",
        )
        assert run.status == "completed"
        assert "Custom essay body" in seen.get("draft", "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_cancel_run_workers_cancels_active_tasks() -> None:
    loop = FactoryLoop()
    started = asyncio.Event()

    async def slow_worker() -> None:
        started.set()
        await asyncio.sleep(60)

    loop._run_workers["run-run-1"] = asyncio.create_task(slow_worker())
    loop._run_workers["queue-2"] = asyncio.create_task(slow_worker())
    loop._reserved_pullers.add("puller-x")

    cancelled = loop.cancel_run_workers(run_ids=["run-1"], queue_item_ids=[2])
    assert cancelled == 2
    assert loop._reserved_pullers == set()
    assert "run-run-1" not in loop._run_workers
    assert "queue-2" not in loop._run_workers


def test_clear_reserved_pullers() -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("puller-a")
    loop.clear_reserved_pullers()
    assert loop._reserved_pullers == set()


@pytest.mark.asyncio
async def test_dispatch_skips_cancelled_run(configured_db, monkeypatch) -> None:
    from article_factory.services.run_control import request_run_cancel

    loop = FactoryLoop()
    continued: list[str] = []

    async def track_continue(self, run_id: str) -> None:
        continued.append(run_id)

    monkeypatch.setattr(FactoryLoop, "_continue_run", track_continue)

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="cancelled-run",
                topic_slug="sports",
                status="running",
                current_step="writer",
            )
        )
        db.commit()
    finally:
        db.close()

    await request_run_cancel("cancelled-run")
    await loop._dispatch_tick()
    assert continued == []


@pytest.mark.asyncio
async def test_dispatch_returns_without_model_or_control_plane(configured_db) -> None:
    loop = FactoryLoop()
    started: list[int] = []

    async def fake_pipeline(db, **kwargs):
        started.append(1)
        return FactoryRun(run_id="x", topic_slug="sports", status="completed")

    loop._spawn_worker = lambda key, coro: started.append(2)  # type: ignore[method-assign]

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"control_plane_url": "", "default_model": ""})
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    assert started == []


@pytest.mark.asyncio
async def test_dispatch_handles_puller_list_failure(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(side_effect=RuntimeError("cp down"))
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    assert loop._run_workers == {}


@pytest.mark.asyncio
async def test_dispatch_clears_stale_puller_reservations(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    started: list[str | None] = []

    async def fake_pipeline(
        db,
        *,
        topic_slug,
        topic_prompt,
        queue_item_id=None,
        selected_puller=None,
        flow_path=None,
        flow_version_id=None,
    ):
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
    loop._reserved_pullers.add("stale-puller")

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert started == ["puller-1"]
    assert "stale-puller" not in loop._reserved_pullers


@pytest.mark.asyncio
async def test_dispatch_skips_puller_without_name(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    started: list[int] = []

    async def fake_pipeline(db, **kwargs):
        started.append(1)
        return FactoryRun(run_id="x", topic_slug="sports", status="completed")

    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", fake_pipeline)

    mock_cp = AsyncMock()
    mock_cp.list_pullers = AsyncMock(
        return_value=[
            {
                "puller_name": "",
                "is_active": True,
                "is_stale": False,
                "status": "idle",
                "supported_models": ["test-model"],
            }
        ]
    )
    monkeypatch.setattr("article_factory.orchestrator.runner.ControlPlaneClient", lambda **kwargs: mock_cp)

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.add(TopicQueueItem(topic_slug="sports", prompt="Queued"))
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert started == []


@pytest.mark.asyncio
async def test_continue_run_missing_run(configured_db) -> None:
    loop = FactoryLoop()
    await loop._continue_run("missing-run-id")


@pytest.mark.asyncio
async def test_continue_active_run_restarts_without_pipeline_state(
    configured_db, pipeline_env, monkeypatch
) -> None:
    executed: list[str] = []

    async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
        executed.append(topic_prompt)
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Resume me", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="restart-run",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.commit()

        handled = await continue_active_run(db, run)
        assert handled is True
        assert executed == ["Resume me"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_resumes_with_pipeline_state(
    configured_db, pipeline_env, monkeypatch
) -> None:
    resumed_from: list[str | None] = []

    async def fake_execute(db, *, run, topic_prompt, resume_from_step=None):
        resumed_from.append(resume_from_step)
        run.status = "completed"
        db.commit()
        return run

    monkeypatch.setattr("article_factory.orchestrator.runner._execute_pipeline", fake_execute)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="resume-run",
            topic_slug="sports",
            status="running",
            current_step="writer",
            pipeline_state={"step_outputs": {}, "feedback": "", "step_records": []},
        )
        db.add(run)
        db.commit()

        handled = await continue_active_run(db, run)
        assert handled is True
        assert resumed_from == ["writer"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_continue_active_run_non_running_returns_true(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="done-run", topic_slug="sports", status="completed")
        db.add(run)
        db.commit()
        assert await continue_active_run(db, run) is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_ensure_running_restarts_stopped_loop(monkeypatch) -> None:
    loop = FactoryLoop()
    started = asyncio.Event()

    async def fake_start(self) -> None:
        started.set()
        self._running = True
        self._task = asyncio.create_task(asyncio.sleep(60))

    monkeypatch.setattr(FactoryLoop, "start", fake_start)
    loop._task = asyncio.create_task(asyncio.sleep(0))
    await loop._task
    loop._task = None

    await loop.ensure_running()
    assert started.is_set()
    loop._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop._task


@pytest.mark.asyncio
async def test_factory_loop_stop_cancels_workers() -> None:
    loop = FactoryLoop()

    async def worker() -> None:
        await asyncio.sleep(60)

    worker_task = asyncio.create_task(worker())
    loop._running = True
    loop._run_workers["run-1"] = worker_task
    loop._reserved_pullers.add("puller-a")
    loop._task = asyncio.create_task(asyncio.sleep(60))

    await loop.stop()
    assert loop._running is False
    assert loop._run_workers == {}
    assert loop._reserved_pullers == set()
    with pytest.raises(asyncio.CancelledError):
        await worker_task


@pytest.mark.asyncio
async def test_run_queue_item_clears_puller_reservation(configured_db, monkeypatch) -> None:
    loop = FactoryLoop()
    loop._reserved_pullers.add("puller-z")

    async def fake_pipeline(db, **kwargs):
        return FactoryRun(run_id="run-q", topic_slug="sports", status="completed")

    monkeypatch.setattr("article_factory.orchestrator.runner.run_pipeline_for_topic", fake_pipeline)

    await loop._run_queue_item(1, "sports", "Prompt", "", "puller-z", None)
    assert "puller-z" not in loop._reserved_pullers


@pytest.mark.asyncio
async def test_dispatch_passes_flow_version_from_queue(configured_db, monkeypatch) -> None:
    from article_factory.models import FlowQueue
    from article_factory.services.flow_versions import ensure_flow_version_for_run

    loop = FactoryLoop()
    captured: list[int | None] = []

    async def fake_pipeline(
        db,
        *,
        topic_slug,
        topic_prompt,
        queue_item_id=None,
        selected_puller=None,
        flow_path=None,
        flow_version_id=None,
    ):
        captured.append(flow_version_id)
        return FactoryRun(run_id=f"run-{queue_item_id}", topic_slug=topic_slug, status="completed")

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

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        version = ensure_flow_version_for_run(db, "sports/standard-4-step.flow.json")
        version_id = version.id
        queue = FlowQueue(
            slug="versioned",
            name="Versioned",
            flow_path="sports/standard-4-step.flow.json",
            flow_version_id=version.id,
            topic_slug="sports",
        )
        db.add(queue)
        db.flush()
        db.add(
            TopicQueueItem(
                flow_queue_id=queue.id,
                topic_slug="sports",
                prompt="Versioned queue",
            )
        )
        db.commit()
    finally:
        db.close()

    await loop._dispatch_tick()
    await asyncio.gather(*loop._run_workers.values(), return_exceptions=True)
    assert captured == [version_id]


@pytest.mark.asyncio
async def test_factory_loop_tick_handles_exception(monkeypatch) -> None:
    loop = FactoryLoop()
    loop._running = True
    ticks = 0

    async def boom_tick(self) -> None:
        nonlocal ticks
        ticks += 1
        raise RuntimeError("tick failed")

    async def stop_after_wait(self) -> None:
        loop._running = False

    monkeypatch.setattr(FactoryLoop, "_dispatch_tick", boom_tick)
    monkeypatch.setattr(FactoryLoop, "_wait_for_next_tick", stop_after_wait)

    await loop._loop()
    assert ticks == 1


@pytest.mark.asyncio
async def test_request_dispatch_noop_when_not_running() -> None:
    loop = FactoryLoop()
    loop._running = False
    loop.request_dispatch()
    assert loop._dispatch_event is None


@pytest.mark.asyncio
async def test_cancel_run_workers_skips_done_tasks() -> None:
    loop = FactoryLoop()

    async def quick() -> None:
        return None

    task = asyncio.create_task(quick())
    await task
    loop._run_workers["run-done"] = task

    cancelled = loop.cancel_run_workers(run_ids=["done"], queue_item_ids=[])
    assert cancelled == 0


@pytest.mark.asyncio
async def test_continue_active_run_fails_interrupted_run(
    configured_db, pipeline_env, monkeypatch
) -> None:
    from article_factory.models import StepExecution

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="interrupted-run",
            topic_slug="sports",
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="interrupted-run",
                step_key="writer",
                status="waiting",
                puller="gpu-01",
                model="test-model",
            )
        )
        db.commit()

        handled = await continue_active_run(db, run)
        assert handled is True
        db.refresh(run)
        assert run.status == "failed"
        assert "interrupted" in (run.error or "").lower()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_ensure_running_noop_when_task_alive(monkeypatch) -> None:
    loop = FactoryLoop()
    started = asyncio.Event()

    async def fake_start(self) -> None:
        started.set()

    monkeypatch.setattr(FactoryLoop, "start", fake_start)
    loop._task = asyncio.create_task(asyncio.sleep(60))

    await loop.ensure_running()
    assert not started.is_set()
    loop._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop._task
