from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from article_factory.models import FactoryRun, StepExecution
from article_factory.services.step_trace import (
    StepTracer,
    duration_ms_between,
    enrich_steps_with_responses,
    step_execution_to_dict,
    step_executions_payload,
)


def test_duration_ms_between_none_start() -> None:
    assert duration_ms_between(None) is None


def test_duration_ms_between_naive_datetimes() -> None:
    start = datetime(2026, 1, 1, 12, 0, 0)
    end = datetime(2026, 1, 1, 12, 0, 2)
    assert duration_ms_between(start, end) == 2000


def test_step_tracer_record_task_status_branches(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-cp-status", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-cp-status", step_key="writer", puller="p1", model="m1")
        tracer.record_task_status("not-a-dict")  # type: ignore[arg-type]

        tracer.record_task_status(
            {
                "status": "queued",
                "queue_depth_at_submit": 4,
            }
        )
        assert "depth 4" in (tracer.execution.progress or {}).get("activity", "")

        tracer.record_task_status(
            {
                "status": "fetched",
                "fetched_by": "gpu-02",
            }
        )
        assert "gpu-02" in (tracer.execution.progress or {}).get("activity", "")

        tracer.record_task_status(
            {
                "status": "failed",
                "response_error": "OOM",
                "response_error_kind": "gpu",
            }
        )
        assert "gpu" in (tracer.execution.progress or {}).get("activity", "")
    finally:
        db.close()


def test_step_tracer_mark_waiting_only_when_submitted(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-wait-skip", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-wait-skip", step_key="writer", puller="p1", model="m1")
        tracer.mark_waiting()
        assert tracer.execution.status == "pending"
    finally:
        db.close()


def test_step_tracer_mark_pulled_from_waiting(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-pulled", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-pulled", step_key="writer", puller="p1", model="m1")
        tracer.mark_submitted(agent_id="a", conversation_id="c", queue_depth=1)
        tracer.mark_waiting()
        tracer.mark_pulled()
        assert tracer.execution.status == "pulled"
        assert tracer.execution.pulled_at is not None
    finally:
        db.close()


def test_step_tracer_record_activity_default_round(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-activity", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-activity", step_key="writer", puller="p1", model="m1")
        tracer.record_activity("Thinking")
        assert (tracer.execution.progress or {}).get("activity") == "Thinking"
    finally:
        db.close()


def test_step_tracer_record_tool_start_with_detail(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-tool", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-tool", step_key="writer", puller="p1", model="m1")
        tracer.record_tool_start("web_search", {"query": "news"}, round_num=2)
        assert "news" in (tracer.execution.progress or {}).get("activity", "")
    finally:
        db.close()


def test_step_tracer_mark_completed_computes_duration(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-duration", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-duration", step_key="writer", puller="p1", model="m1")
        tracer.execution.started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        tracer.mark_completed(response_content="done", turns=1)
        assert tracer.execution.duration_ms is not None
        assert tracer.execution.duration_ms >= 0
    finally:
        db.close()


def test_step_tracer_force_commit_schedules_showroom_refresh(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-refresh", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-refresh", step_key="writer", puller="p1", model="m1")
        with patch(
            "article_factory.services.showroom_status_sync.schedule_showroom_status_refresh"
        ) as schedule:
            tracer.mark_submitted(agent_id="a", conversation_id="c", queue_depth=1)
            schedule.assert_called_once()
    finally:
        db.close()


def test_enrich_steps_from_manifest(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-manifest",
                topic_slug="sports",
                status="completed",
                manifest={
                    "steps": [
                        {"step_key": "writer", "content": "From manifest"},
                    ]
                },
            )
        )
        db.add(
            StepExecution(
                run_id="run-manifest",
                step_key="writer",
                status="completed",
            )
        )
        db.commit()
        steps = enrich_steps_with_responses(
            db,
            "run-manifest",
            [step_execution_to_dict(db.query(StepExecution).one())],
        )
        assert steps[0]["response_content"] == "From manifest"
    finally:
        db.close()


def test_enrich_steps_by_key_when_pipeline_incomplete(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-by-key",
                topic_slug="sports",
                status="running",
                pipeline_state={
                    "step_records": [
                        {"step_key": "writer", "content": "First draft"},
                        {"step_key": "writer", "content": "Second draft"},
                    ]
                },
            )
        )
        db.commit()
        steps = [
            {"step_key": "writer", "status": "completed"},
            {"step_key": "writer", "status": "completed"},
        ]
        enriched = enrich_steps_with_responses(db, "run-by-key", steps)
        assert enriched[0]["response_content"] == "First draft"
        assert enriched[1]["response_content"] == "Second draft"
    finally:
        db.close()


def test_enrich_steps_fills_usage_and_duration(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-meta",
                topic_slug="sports",
                status="running",
                pipeline_state={
                    "step_records": [
                        {
                            "step_key": "writer",
                            "content": "Body",
                            "duration_ms": 99,
                            "usage": {"total_tokens": 10},
                            "tools_used": [{"tool": "web_search"}],
                            "turns": 2,
                        }
                    ]
                },
            )
        )
        db.commit()
        steps = [{"step_key": "writer", "status": "completed"}]
        enriched = enrich_steps_with_responses(db, "run-meta", steps)
        assert enriched[0]["duration_ms"] == 99
        assert enriched[0]["usage"]["total_tokens"] == 10
        assert enriched[0]["tools_used"][0]["tool"] == "web_search"
        assert enriched[0]["turns"] == 2
    finally:
        db.close()


def test_step_executions_payload(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-payload", topic_slug="sports", status="running"))
        db.add(
            StepExecution(
                run_id="run-payload",
                step_key="writer",
                status="completed",
                response_content="Done",
            )
        )
        db.commit()
        payload = step_executions_payload(db, "run-payload")
        assert len(payload) == 1
        assert payload[0]["response_content"] == "Done"
    finally:
        db.close()
