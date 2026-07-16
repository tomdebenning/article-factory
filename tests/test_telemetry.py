from __future__ import annotations

from datetime import datetime, timezone

import article_factory.db as db_module
from article_factory.models import FactoryRun
from article_factory.services.flow_schema import FlowDefinition, FlowStepCompletion, new_flow_step
from article_factory.services.flow_storage import create_flow
from article_factory.services.flow_versions import create_flow_version
from article_factory.services.telemetry import (
    capture_run_telemetry,
    capture_run_telemetry_safe,
    list_flow_telemetry_summary,
    rebuild_flow_telemetry,
    rebuild_run_telemetry,
)
from article_factory.services.telemetry_csv import build_telemetry_csv, csv_headers


def _writer_review_manifest(*, reviews: list[str], writers: list[str] | None = None) -> dict:
    writers = writers or [f"draft {index}" for index in range(1, len(reviews) + 1)]
    steps = []
    for index, review in enumerate(reviews):
        steps.append({"step_key": "step_1", "content": writers[index], "duration_ms": 1000, "usage": {"total_tokens": 100}, "turns": 1})
        steps.append(
            {
                "step_key": "step_2",
                "content": (
                    "ARTICLE REVIEW\n\nAccuracy & Verifiable Facts\n\n35 / 40\n\n"
                    f"TOTAL SCORE: {80 + index}/100\n\nVERDICT: {review}\nEND REVIEW"
                ),
                "duration_ms": 2000,
                "usage": {"total_tokens": 200},
                "turns": 2,
            }
        )
    return {"steps": steps, "step_stats": steps}


def _make_step_flow(configured_db: str) -> tuple[str, int]:
    db = db_module.SessionLocal()
    try:
        rel_path, flow = create_flow(folder="", slug="telemetry-test", display_name="Telemetry", step_count=2)
        flow.steps[0].step_key = "step_1"
        flow.steps[1].step_key = "step_2"
        flow.steps[1].completion = FlowStepCompletion(
            can_complete=True,
            can_loop=True,
            loop_goto_step_id=flow.steps[0].step_id,
        )
        from article_factory.services.flow_storage import write_flow

        write_flow(rel_path, flow)
        version = create_flow_version(db, rel_path, message="v1")
        db.commit()
        return rel_path, version.id
    finally:
        db.close()


def test_capture_run_telemetry_first_pass(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        started = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        finished = datetime(2026, 7, 10, 12, 10, tzinfo=timezone.utc)
        db.add(
            FactoryRun(
                run_id="run-telemetry-1",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                selected_model="model-a",
                selected_puller="puller-a",
                first_pass_accept=True,
                started_at=started,
                finished_at=finished,
                manifest=_writer_review_manifest(reviews=["ACCEPTED"]),
            )
        )
        db.commit()
        row = capture_run_telemetry(db, "run-telemetry-1")
        assert row is not None
        assert row.success is True
        assert row.accepted is True
        assert row.initial_score == 80
        assert row.final_score == 80
        assert row.iteration_count == 1
        assert row.wall_clock_duration_ms == 600_000
        assert row.total_duration_ms == 3000
    finally:
        db.close()


def test_capture_failed_run(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-telemetry-failed",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="failed",
                error="Max flow iterations exceeded",
                manifest=_writer_review_manifest(reviews=["REJECTED", "REJECTED"]),
            )
        )
        db.commit()
        row = capture_run_telemetry(db, "run-telemetry-failed")
        assert row is not None
        assert row.success is False
        assert row.termination_reason == "max_iterations"
        assert row.regression_count == 0
        assert row.score_change == 1
    finally:
        db.close()


def test_idempotent_rebuild(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-telemetry-idem",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                manifest=_writer_review_manifest(reviews=["ACCEPTED"]),
            )
        )
        db.commit()
        first = capture_run_telemetry(db, "run-telemetry-idem")
        second = rebuild_run_telemetry(db, "run-telemetry-idem")
        assert first is not None and second is not None
        assert first.id == second.id
    finally:
        db.close()


def test_csv_export_shape_and_injection(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-telemetry-csv",
                topic_slug="=cmd",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="failed",
                error="@injection",
                manifest=_writer_review_manifest(reviews=["REJECTED"]),
            )
        )
        db.commit()
        capture_run_telemetry(db, "run-telemetry-csv")
        from article_factory.services.telemetry import get_flow_telemetry_rows

        rows = get_flow_telemetry_rows(db, rel_path, version_id)
        csv_text = build_telemetry_csv(db, rows)
        assert "run_id" in csv_headers()
        assert "iteration_1_score" in csv_text
        assert "iteration_scores_json" in csv_text
        assert "'=cmd" in csv_text
        assert "'@injection" in csv_text
        assert "draft 1" not in csv_text
    finally:
        db.close()


def test_telemetry_api(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-telemetry-api",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                manifest=_writer_review_manifest(reviews=["ACCEPTED"]),
            )
        )
        db.commit()
    finally:
        db.close()

    missing = client.get("/api/flows/telemetry/export", headers=api_headers)
    assert missing.status_code == 422

    export = client.get(
        f"/api/flows/telemetry/export?path={rel_path}&flow_version_id={version_id}",
        headers=api_headers,
    )
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "run-telemetry-api" in export.text

    export_query = client.get(
        f"/api/flows/telemetry/export?path={rel_path}&flow_version_id={version_id}&api_key=test-factory-key",
    )
    assert export_query.status_code == 200

    export_cookie = client.get(
        f"/api/flows/telemetry/export?path={rel_path}&flow_version_id={version_id}",
        cookies={"factory_api_key": "test-factory-key"},
    )
    assert export_cookie.status_code == 200

    listing = client.get(
        f"/api/flows/telemetry?path={rel_path}&flow_version_id={version_id}",
        headers=api_headers,
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 1


def test_capture_skips_non_terminal_run(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-telemetry-running",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="running",
            )
        )
        db.commit()
        assert capture_run_telemetry(db, "run-telemetry-running") is None
    finally:
        db.close()


def test_capture_cancelled_run(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-telemetry-cancelled",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="cancelled",
            )
        )
        db.commit()
        row = capture_run_telemetry(db, "run-telemetry-cancelled")
        assert row is not None
        assert row.termination_reason == "cancelled"
    finally:
        db.close()


def test_capture_run_telemetry_safe_swallows_errors(configured_db, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("telemetry boom")

    monkeypatch.setattr("article_factory.services.telemetry.capture_run_telemetry", boom)
    db = db_module.SessionLocal()
    try:
        capture_run_telemetry_safe(db, "any-run")
    finally:
        db.close()


def test_rebuild_flow_telemetry(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-rebuild-flow",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                manifest=_writer_review_manifest(reviews=["ACCEPTED"]),
            )
        )
        db.commit()
        stats = rebuild_flow_telemetry(db, rel_path, version_id)
        assert stats["parsed"] == 1
        assert stats["total"] == 1
    finally:
        db.close()


def test_list_flow_telemetry_summary_filters(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-filter-a",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                selected_model="model-a",
                manifest=_writer_review_manifest(reviews=["ACCEPTED"]),
            )
        )
        db.add(
            FactoryRun(
                run_id="run-filter-b",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="failed",
                selected_model="model-b",
                error="failed",
                manifest=_writer_review_manifest(reviews=["REJECTED"]),
            )
        )
        db.commit()
        capture_run_telemetry(db, "run-filter-a")
        capture_run_telemetry(db, "run-filter-b")
        total, items = list_flow_telemetry_summary(
            db,
            flow_path=rel_path,
            flow_version_id=version_id,
            status="completed",
            model="model-a",
        )
        assert total == 1
        assert items[0]["run_id"] == "run-filter-a"
    finally:
        db.close()
