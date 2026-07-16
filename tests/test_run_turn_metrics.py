from __future__ import annotations

from article_factory.models import FactoryRun, StepExecution
from article_factory.services.run_turn_metrics import review_cycles_for_run, turn_metrics_for_runs


def test_review_cycles_counts_review_steps(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="cycles-test",
            topic_slug="general",
            flow_path="test/flow.flow.json",
            status="completed",
            manifest={
                "steps": [
                    {"step_key": "writer", "content": "draft 1"},
                    {"step_key": "review", "content": "VERDICT: REJECT"},
                    {"step_key": "writer", "content": "draft 2"},
                    {"step_key": "review", "content": "VERDICT: ACCEPT"},
                ],
                "production": {"review_round": 1, "iteration_count": 2},
            },
        )
        assert review_cycles_for_run(run) == 2

        first_pass = FactoryRun(
            run_id="cycles-first",
            topic_slug="general",
            flow_path="test/flow.flow.json",
            status="completed",
            manifest={
                "steps": [
                    {"step_key": "writer", "content": "draft"},
                    {"step_key": "review", "content": "VERDICT: ACCEPT"},
                ],
                "production": {"review_round": 0, "iteration_count": 1},
            },
        )
        assert review_cycles_for_run(first_pass) == 1
    finally:
        db.close()


def test_turn_metrics_exclude_failed_zero_cycles(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        runs = [
            FactoryRun(run_id="c1", topic_slug="g", flow_path="f", status="failed", review_round=0),
            FactoryRun(
                run_id="c2",
                topic_slug="g",
                flow_path="f",
                status="completed",
                manifest={
                    "steps": [
                        {"step_key": "writer", "content": "d"},
                        {"step_key": "review", "content": "VERDICT: ACCEPT", "turns": 2},
                    ]
                },
            ),
            FactoryRun(
                run_id="c3",
                topic_slug="g",
                flow_path="f",
                status="completed",
                manifest={
                    "steps": [
                        {"step_key": "writer", "content": "d1"},
                        {"step_key": "review", "content": "VERDICT: REJECT", "turns": 2},
                        {"step_key": "writer", "content": "d2"},
                        {"step_key": "review", "content": "VERDICT: ACCEPT", "turns": 3},
                    ]
                },
            ),
        ]
        for run in runs:
            db.add(run)
        db.commit()

        metrics = turn_metrics_for_runs(runs, db)
        assert metrics["median_review_rounds"] == 1.5
        assert metrics["avg_review_rounds"] == 1.5
    finally:
        db.close()


def test_review_cycles_from_step_executions(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="cycles-exec", topic_slug="g", flow_path="f", status="completed")
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="cycles-exec",
                step_key="review",
                status="completed",
                response_content="VERDICT: ACCEPT",
            )
        )
        db.commit()
        assert review_cycles_for_run(run, db) == 1
    finally:
        db.close()
