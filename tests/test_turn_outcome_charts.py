from __future__ import annotations

from article_factory.models import FactoryRun, StepExecution
from article_factory.services.turn_outcome_charts import build_turn_outcome_charts, outcome_cycle_for_run


def test_outcome_cycle_completed(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="turn-ok",
            topic_slug="general",
            flow_path="f",
            status="completed",
            manifest={
                "steps": [
                    {"step_key": "writer", "content": "d1"},
                    {"step_key": "review", "content": "VERDICT: REJECT"},
                    {"step_key": "writer", "content": "d2"},
                    {"step_key": "review", "content": "VERDICT: ACCEPT"},
                ]
            },
        )
        assert outcome_cycle_for_run(run, db) == 2
    finally:
        db.close()


def test_outcome_cycle_failed_on_writer(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="turn-fail-w", topic_slug="general", flow_path="f", status="failed", error="timeout")
        db.add(run)
        db.flush()
        db.add(StepExecution(run_id="turn-fail-w", step_key="writer", status="failed"))
        db.commit()
        assert outcome_cycle_for_run(run, db) == 1
    finally:
        db.close()


def test_build_turn_outcome_charts(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        runs = [
            FactoryRun(
                run_id="chart-ok-1",
                topic_slug="g",
                flow_path="f",
                status="completed",
                manifest={
                    "steps": [
                        {"step_key": "writer", "content": "d"},
                        {"step_key": "review", "content": "VERDICT: ACCEPT"},
                    ]
                },
            ),
            FactoryRun(
                run_id="chart-ok-2",
                topic_slug="g",
                flow_path="f",
                status="completed",
                manifest={
                    "steps": [
                        {"step_key": "writer", "content": "d1"},
                        {"step_key": "review", "content": "VERDICT: REJECT"},
                        {"step_key": "writer", "content": "d2"},
                        {"step_key": "review", "content": "VERDICT: ACCEPT"},
                    ]
                },
            ),
            FactoryRun(run_id="chart-fail-1", topic_slug="g", flow_path="f", status="failed", error="x"),
        ]
        for run in runs:
            db.add(run)
        db.flush()
        db.add(StepExecution(run_id="chart-fail-1", step_key="review", status="failed"))
        db.commit()

        charts = build_turn_outcome_charts(runs, db)
        assert charts["success_total"] == 2
        assert charts["failure_total"] == 1
        success = {row["turn"]: row["count"] for row in charts["success_by_turn"]}
        assert success[1] == 1
        assert success[2] == 1
        failure = {row["turn"]: row["count"] for row in charts["failure_by_turn"]}
        assert failure[1] == 1
    finally:
        db.close()
