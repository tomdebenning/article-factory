from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.orchestrator.pipeline import (
    build_manifest,
    new_run_id,
    push_factory_status,
    serialize_active_run,
)


def test_new_run_id_format() -> None:
    run_id = new_run_id()
    assert run_id.startswith("run-")


def test_build_manifest_includes_stats(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-manifest",
            topic_slug="sports",
            status="completed",
            draft_number=1,
            review_round=0,
        )
        db.add(run)
        db.commit()
        manifest = build_manifest(
            run,
            [
                {
                    "step_key": "writer",
                    "content": "Body",
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }
            ],
        )
        assert manifest["stats"]["total_tokens"] == 2
        assert not manifest["tool_use"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_push_factory_status_with_db_runs(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Prompt text", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-status",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run)
        db.commit()

        cms = AsyncMock()
        await push_factory_status(
            cms,
            db=db,
            state="running",
            active_run=run,
            active_runs=[run],
            queue_depth=3,
            topic_slug="sports",
        )
        payload = cms.put_factory_status.await_args.args[0]
        assert payload["queue_depth"] == 3
        assert payload["active_runs"][0]["topic_prompt"] == "Prompt text"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_push_factory_status_without_db(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-simple",
            topic_slug="sports",
            status="running",
            current_step="writer",
        )
        cms = AsyncMock()
        await push_factory_status(
            cms,
            state="running",
            active_run=run,
            queue_depth=0,
            topic_slug="sports",
        )
        payload = cms.put_factory_status.await_args.args[0]
        assert payload["active_run"]["run_id"] == "run-simple"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_push_factory_status_idle(configured_db) -> None:
    cms = AsyncMock()
    await push_factory_status(cms, state="idle", active_run=None, queue_depth=0)
    payload = cms.put_factory_status.await_args.args[0]
    assert payload["active_run"] is None
    assert payload["active_runs"] == []


def test_serialize_active_run_fallback_step(configured_db, monkeypatch) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-serialize",
            topic_slug="sports",
            status="running",
            current_step="writer",
            flow_path="sports/standard-4-step.flow.json",
        )
        db.add(run)
        db.commit()

        def boom(_db, _run_id):
            raise RuntimeError("db")

        monkeypatch.setattr(
            "article_factory.services.step_trace.step_executions_payload",
            boom,
        )
        payload = serialize_active_run(db, run)
        assert payload["steps"][0]["step_key"] == "writer"
        assert payload["flow_steps"]
    finally:
        db.close()
