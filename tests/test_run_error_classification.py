from __future__ import annotations

from article_factory.models import FactoryRun, RunErrorTag, StepExecution
from article_factory.services.run_error_classification import (
    classify_run_error,
    error_group_label,
    load_manual_error_tags,
    resolve_run_error_group,
    step_errors_for_run,
)


def test_classify_iteration_limit() -> None:
    run = FactoryRun(run_id="r1", topic_slug="t", status="failed", error="Max flow iterations exceeded")
    assert classify_run_error(run) == "iteration_limit"


def test_classify_missing_verdict_from_error() -> None:
    run = FactoryRun(
        run_id="r2",
        topic_slug="t",
        status="failed",
        error="Last step response missing VERDICT: ACCEPT or REJECT",
    )
    assert classify_run_error(run) == "missing_verdict"


def test_classify_missing_verdict_from_manifest() -> None:
    run = FactoryRun(
        run_id="r3",
        topic_slug="t",
        status="failed",
        manifest={"steps": [{"step_key": "review", "content": "Looks fine but no verdict line"}]},
    )
    assert classify_run_error(run) == "missing_verdict"


def test_classify_puller_timeout() -> None:
    run = FactoryRun(run_id="r4", topic_slug="t", status="failed", error="No puller picked up task within 600s")
    assert classify_run_error(run) == "puller_timeout"


def test_classify_completed() -> None:
    run = FactoryRun(run_id="r5", topic_slug="t", status="completed")
    assert classify_run_error(run) == "completed"


def test_resolve_manual_override() -> None:
    from article_factory.models import RunErrorTag

    run = FactoryRun(run_id="r6", topic_slug="t", status="failed", error="Max flow iterations exceeded")
    manual = RunErrorTag(run_id="r6", error_group="llm_error", note="bad xml")
    info = resolve_run_error_group(run, manual_tags={"r6": manual})
    assert info["error_group"] == "llm_error"
    assert info["auto_error_group"] == "iteration_limit"
    assert info["manual_note"] == "bad xml"


def test_classify_running_and_cancelled() -> None:
    assert classify_run_error(FactoryRun(run_id="r", topic_slug="t", status="running")) == "running"
    assert classify_run_error(FactoryRun(run_id="r", topic_slug="t", status="cancelled")) == "cancelled"


def test_classify_run_interrupted() -> None:
    run = FactoryRun(run_id="r", topic_slug="t", status="failed", error="Run interrupted after factory restarted")
    assert classify_run_error(run) == "run_interrupted"


def test_classify_llm_error() -> None:
    run = FactoryRun(run_id="r", topic_slug="t", status="failed", error="llm_transport: HTTP 502 from model")
    assert classify_run_error(run) == "llm_error"


def test_classify_from_step_errors() -> None:
    run = FactoryRun(run_id="r", topic_slug="t", status="failed", error="unknown")
    assert classify_run_error(run, step_errors=["ollama timeout"]) == "llm_error"
    assert classify_run_error(run, step_errors=["puller did not respond"]) == "puller_timeout"


def test_classify_failed_other() -> None:
    run = FactoryRun(run_id="r", topic_slug="t", status="failed", error="something else")
    assert classify_run_error(run) == "failed_other"


def test_classify_step_records_from_pipeline_state() -> None:
    run = FactoryRun(
        run_id="r",
        topic_slug="t",
        status="failed",
        pipeline_state={"step_records": [{"step_key": "review", "content": "No verdict here"}]},
    )
    assert classify_run_error(run) == "missing_verdict"


def test_error_group_label_unknown() -> None:
    assert error_group_label("custom_group") == "Custom Group"


def test_load_manual_error_tags_empty(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        assert load_manual_error_tags(db, []) == {}
    finally:
        db.close()


def test_step_errors_for_run(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        db.add(StepExecution(run_id="err-run", step_key="writer", status="failed", error="boom"))
        db.commit()
        assert step_errors_for_run(db, "err-run") == ["boom"]
    finally:
        db.close()
