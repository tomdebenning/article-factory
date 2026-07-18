from __future__ import annotations

import pytest

from article_factory.models import FactoryRun, StepExecution, TopicQueueItem
from article_factory.services.run_recovery import (
    ensure_run_pipeline_state,
    reconcile_orphaned_runs,
    reconstruct_pipeline_state,
)


def test_reconcile_orphaned_runs_fails_interrupted_run(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Topic", status="running")
        db.add(item)
        db.flush()
        run = FactoryRun(
            run_id="run-orphan",
            topic_slug="sports",
            queue_item_id=item.id,
            status="running",
            current_step="writer",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="run-orphan",
                step_key="writer",
                status="pulled",
                puller="puller-01",
                model="llama3",
            )
        )
        db.commit()

        failed = reconcile_orphaned_runs(db)
        assert failed == 0
        db.refresh(run)
        db.refresh(item)
        assert run.status == "running"
        assert item.status == "running"
    finally:
        db.close()


def test_reconcile_orphaned_runs_keeps_resumable_run(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-resume",
            topic_slug="sports",
            status="running",
            current_step="fact_asserter",
            pipeline_state={"draft": "# Title\n\nBody", "step_records": []},
        )
        db.add(run)
        db.commit()

        failed = reconcile_orphaned_runs(db)
        assert failed == 0
        db.refresh(run)
        assert run.status == "running"
    finally:
        db.close()


def test_reconstruct_pipeline_state_after_writer_before_review(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.flow_defaults import build_writer_review_flow
    from article_factory.services.flow_storage import write_flow

    write_flow("test/writer-review.flow.json", build_writer_review_flow())
    db = SessionLocal()
    try:
        flow_path = "test/writer-review.flow.json"
        run = FactoryRun(
            run_id="run-loop-resume",
            topic_slug="sports",
            status="running",
            current_step="review",
            flow_path=flow_path,
            review_round=0,
            draft_number=1,
        )
        db.add(run)
        db.flush()
        db.add_all(
            [
                StepExecution(
                    run_id="run-loop-resume",
                    step_key="writer",
                    status="completed",
                    puller="puller-01",
                    model="llama3",
                    response_content="# Draft v1",
                ),
                StepExecution(
                    run_id="run-loop-resume",
                    step_key="review",
                    status="completed",
                    puller="puller-01",
                    model="llama3",
                    response_content="Needs work.\n\nVERDICT: REJECT",
                ),
                StepExecution(
                    run_id="run-loop-resume",
                    step_key="writer",
                    status="completed",
                    puller="puller-01",
                    model="llama3",
                    response_content="# Draft v2",
                ),
            ]
        )
        db.commit()

        failed = reconcile_orphaned_runs(db)
        assert failed == 0
        db.refresh(run)
        assert run.status == "running"
        assert run.pipeline_state is not None
        assert run.pipeline_state["current_step_id"] == "00000000-0000-4000-8000-000000000202"
        assert run.pipeline_state["iteration"] == 1
        assert run.pipeline_state["feedback"].startswith("Needs work")
        assert run.pipeline_state["step_outputs"]["writer"] == "# Draft v2"
        assert run.review_round == 1
        assert run.draft_number == 2
    finally:
        db.close()


def test_reconstruct_pipeline_state_returns_none_without_current_step(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.flow_defaults import build_writer_review_flow
    from article_factory.services.flow_storage import write_flow

    write_flow("test/writer-review.flow.json", build_writer_review_flow())
    db = SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-no-step",
            topic_slug="sports",
            status="running",
            current_step="",
            flow_path="test/writer-review.flow.json",
        )
        db.add(run)
        db.commit()
        assert reconstruct_pipeline_state(db, run) is None
        assert ensure_run_pipeline_state(db, run) is False
    finally:
        db.close()
