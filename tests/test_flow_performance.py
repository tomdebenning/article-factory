from __future__ import annotations

from article_factory.services.flow_performance import compute_first_pass_accept, resolve_gate_config
from article_factory.services.flow_schema import FlowDefinition, FlowStepCompletion, new_flow_step
from article_factory.services.flow_versions import create_flow_version, list_flow_versions


def _writer_review_flow() -> FlowDefinition:
    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    review.completion = FlowStepCompletion(
        can_complete=True,
        can_loop=True,
        loop_goto_step_id=writer.step_id,
    )
    return FlowDefinition(slug="test", display_name="Test", steps=[writer, review])


def test_resolve_gate_config_from_loop() -> None:
    flow = _writer_review_flow()
    gate_key, producers = resolve_gate_config(flow)
    assert gate_key == "review"
    assert "writer" in producers


def test_compute_first_pass_accept_true() -> None:
    flow = _writer_review_flow()
    steps = [
        {"step_key": "writer", "content": "draft"},
        {"step_key": "review", "content": "Looks good.\nVERDICT: ACCEPT"},
    ]
    assert compute_first_pass_accept(flow, steps) is True


def test_compute_first_pass_accept_false_on_retry() -> None:
    flow = _writer_review_flow()
    steps = [
        {"step_key": "writer", "content": "draft 1"},
        {"step_key": "review", "content": "Needs work.\nVERDICT: REJECT"},
        {"step_key": "writer", "content": "draft 2"},
        {"step_key": "review", "content": "Better.\nVERDICT: ACCEPT"},
    ]
    assert compute_first_pass_accept(flow, steps) is False


def test_create_flow_version_deduplicates(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.services.flow_storage import create_flow

    rel_path, _flow = create_flow(folder="", slug="perf-test", display_name="Perf Test", step_count=2)
    db = db_module.SessionLocal()
    try:
        first = create_flow_version(db, rel_path, message="v1")
        second = create_flow_version(db, rel_path, message="v1 again")
        assert first.id == second.id
        versions = list_flow_versions(db, rel_path)
        assert len(versions) == 1
        assert versions[0].version_number == 1
    finally:
        db.close()


def test_flow_performance_api(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.flow_storage import create_flow

    rel_path, _flow = create_flow(folder="", slug="perf-api", display_name="Perf API", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="baseline")
        db.add(
            FactoryRun(
                run_id="run-perf-1",
                topic_slug="general",
                flow_path=rel_path,
                status="completed",
                selected_model="model-a",
                flow_version_id=version.id,
                first_pass_accept=True,
                manifest={"steps": [{"step_key": "writer"}, {"step_key": "review", "content": "VERDICT: ACCEPT"}]},
            )
        )
        db.commit()
    finally:
        db.close()

    create = client.post(
        "/api/flows/versions",
        headers=api_headers,
        json={"path": rel_path, "message": "baseline"},
    )
    assert create.status_code == 200

    perf = client.get(f"/api/flows/performance?path={rel_path}", headers=api_headers)
    assert perf.status_code == 200
    body = perf.json()
    assert body["overall"]["first_pass_count"] == 1
    assert body["overall"]["first_pass_rate"] == 1.0

    analyze = client.post(
        "/api/flows/analyze",
        headers=api_headers,
        json={"path": rel_path},
    )
    assert analyze.status_code == 200
    assert analyze.json()["analysis"]["summary"]
