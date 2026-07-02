from __future__ import annotations

import pytest

from article_factory.services.run_control import RunCancelledError
from article_factory.workers.base import render_prompt


def test_step_variables_include_draft_after_writer() -> None:
    draft = "# Oklahoma State\n\nThe team played well."
    fact_prompt = render_prompt(
        "Draft:\n{{draft}}\n\nReport verified and unsupported claims.",
        {"topic": "Sports", "draft": draft, "sources": "", "fact_check": "", "feedback": ""},
    )
    assert "Oklahoma State" in fact_prompt
    assert "The team played well" in fact_prompt


@pytest.mark.asyncio
async def test_stop_run(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun, StepExecution

    db = SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-stop", topic_slug="sports", status="running", current_step="writer"))
        db.add(
            StepExecution(
                run_id="run-stop",
                step_key="writer",
                status="waiting",
                puller="puller-01",
                model="llama3",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post("/api/runs/run-stop/stop", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True

    db = SessionLocal()
    try:
        run = db.query(FactoryRun).filter_by(run_id="run-stop").one()
        assert run.status == "cancelled"
        step = db.query(StepExecution).filter_by(run_id="run-stop").one()
        assert step.status == "failed"
        assert step.error == "Run stopped"
    finally:
        db.close()

    again = client.post("/api/runs/run-stop/stop", headers=api_headers)
    assert again.json()["ok"] is False


@pytest.mark.asyncio
async def test_ensure_run_active_aborts_after_stop(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun
    from article_factory.services.run_control import ensure_run_active, mark_run_cancelled_in_db

    db = SessionLocal()
    try:
        run = FactoryRun(run_id="run-stale", topic_slug="sports", status="running", current_step="writer")
        db.add(run)
        db.commit()

        mark_run_cancelled_in_db(db, run)
        db.commit()

        with pytest.raises(RunCancelledError):
            await ensure_run_active(db, run)
    finally:
        db.close()


def test_delete_run(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun, StepExecution, TopicQueueItem

    db = SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="failed")
        db.add(item)
        db.flush()
        db.add(FactoryRun(run_id="run-del", topic_slug="sports", queue_item_id=item.id, status="failed"))
        db.add(StepExecution(run_id="run-del", step_key="writer", status="completed"))
        db.commit()
        item_id = item.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-live", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    assert client.delete("/api/runs/run-live", headers=api_headers).status_code == 409

    response = client.delete("/api/runs/run-del", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["deleted_run_id"] == "run-del"

    db = SessionLocal()
    try:
        assert db.query(FactoryRun).filter_by(run_id="run-del").one_or_none() is None
        assert db.get(TopicQueueItem, item_id).status == "queued"
    finally:
        db.close()
