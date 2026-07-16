from __future__ import annotations

import article_factory.db as db_module
from article_factory.models import FactoryRun
from article_factory.services.flow_performance import resolve_gate_config
from article_factory.services.flow_schema import FlowDefinition, FlowStepCompletion, new_flow_step
from article_factory.services.flow_storage import create_flow
from article_factory.services.prompt_coach import _collect_reject_samples, analyze_flow_performance


def _writer_review_flow() -> FlowDefinition:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    return FlowDefinition(slug="coach-test", display_name="Coach Test", steps=[writer, review])


def test_collect_reject_samples_dedupes_and_limits() -> None:
    runs = [
        FactoryRun(
            run_id="r1",
            topic_slug="general",
            status="completed",
            manifest={
                "steps": [
                    {"step_key": "review", "content": "Bad.\n\nVERDICT: REJECT"},
                    {"step_key": "review", "content": "Bad.\n\nVERDICT: REJECT"},
                    {"step_key": "review", "content": "Still bad.\n\nVERDICT: REJECT"},
                ]
            },
        )
    ]
    samples = _collect_reject_samples(runs, "review", limit=2)
    assert len(samples) == 2
    assert samples[0] == "Bad."


def test_analyze_low_first_pass_suggests_producer_fix(configured_db) -> None:
    from article_factory.services.flow_storage import write_flow

    rel_path = "test/coach-low.flow.json"
    write_flow(rel_path, _writer_review_flow())
    db = db_module.SessionLocal()
    try:
        for index in range(3):
            db.add(
                FactoryRun(
                    run_id=f"run-low-{index}",
                    topic_slug="general",
                    flow_path=rel_path,
                    status="completed",
                    first_pass_accept=False,
                    manifest={
                        "steps": [
                            {"step_key": "writer", "content": "draft"},
                            {"step_key": "review", "content": "Needs work.\n\nVERDICT: REJECT"},
                            {"step_key": "writer", "content": "draft 2"},
                            {"step_key": "review", "content": "Better.\n\nVERDICT: ACCEPT"},
                        ]
                    },
                )
            )
        db.commit()
        row = analyze_flow_performance(db, flow_path=rel_path)
        assert row.run_count == 3
        assert any(item["step_key"] == "writer" for item in row.suggestions)
    finally:
        db.close()


def test_analyze_healthy_performance_suggestion(configured_db) -> None:
    from article_factory.services.flow_storage import write_flow

    rel_path = "test/coach-healthy.flow.json"
    write_flow(rel_path, _writer_review_flow())
    db = db_module.SessionLocal()
    try:
        for index in range(3):
            db.add(
                FactoryRun(
                    run_id=f"run-healthy-{index}",
                    topic_slug="general",
                    flow_path=rel_path,
                    status="completed",
                    first_pass_accept=True,
                    manifest={
                        "steps": [
                            {"step_key": "writer", "content": "draft"},
                            {"step_key": "review", "content": "Good.\n\nVERDICT: ACCEPT"},
                        ]
                    },
                )
            )
        db.commit()
        row = analyze_flow_performance(db, flow_path=rel_path)
        assert "healthy" in row.suggestions[0]["diagnosis"].lower()
    finally:
        db.close()


def test_analyze_filters_by_flow_version_id(configured_db) -> None:
    from article_factory.services.flow_versions import create_flow_version
    from article_factory.services.flow_storage import read_flow

    rel_path, _flow = create_flow(folder="", slug="coach-filter", display_name="Coach Filter", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="run-filter-a",
                topic_slug="general",
                flow_path=rel_path,
                status="completed",
                flow_version_id=version.id,
                first_pass_accept=True,
                manifest={"steps": [{"step_key": "review", "content": "VERDICT: ACCEPT"}]},
            )
        )
        db.add(
            FactoryRun(
                run_id="run-filter-b",
                topic_slug="general",
                flow_path=rel_path,
                status="completed",
                flow_version_id=None,
                first_pass_accept=True,
                manifest={"steps": [{"step_key": "review", "content": "VERDICT: ACCEPT"}]},
            )
        )
        db.commit()
        row = analyze_flow_performance(db, flow_path=rel_path, flow_version_id=version.id)
        assert row.run_count == 1
    finally:
        db.close()
