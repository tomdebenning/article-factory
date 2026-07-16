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


def test_resolve_gate_config_from_performance_block() -> None:
    from article_factory.services.flow_schema import FlowPerformanceConfig

    flow = _writer_review_flow()
    flow.performance = FlowPerformanceConfig(gate_step_key="review", producer_step_keys=[])
    gate_key, producers = resolve_gate_config(flow)
    assert gate_key == "review"
    assert "writer" in producers


def test_compute_first_pass_no_gate_unique_keys() -> None:
    from article_factory.services.flow_schema import FlowStepCompletion

    writer = new_flow_step(order=1, label="Only", step_key="only")
    writer.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    flow = FlowDefinition(slug="solo", display_name="Solo", steps=[writer])
    assert compute_first_pass_accept(flow, [{"step_key": "only", "content": "ok"}]) is True
    assert compute_first_pass_accept(flow, []) is False


def test_compute_first_pass_from_manifest() -> None:
    from article_factory.services.flow_performance import compute_first_pass_from_manifest

    flow = _writer_review_flow()
    manifest = {
        "steps": [
            {"step_key": "writer", "content": "draft"},
            {"step_key": "review", "content": "Good.\nVERDICT: ACCEPT"},
        ]
    }
    assert compute_first_pass_from_manifest(manifest, flow) is True


def test_apply_run_performance_and_aggregate(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun, TopicQueueItem
    from article_factory.services.flow_performance import (
        aggregate_performance,
        apply_run_performance,
        list_topic_queues_for_flow,
    )
    from article_factory.services.flow_queues import ensure_default_flow_queue
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version
    from article_factory.services.topic_queue_snapshots import get_or_create_topic_queue_snapshot

    rel_path, flow = create_flow(folder="", slug="perf-svc", display_name="Perf Svc", step_count=2)
    review_key = sorted(flow.steps, key=lambda s: s.order)[-1].step_key
    writer_key = sorted(flow.steps, key=lambda s: s.order)[0].step_key
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        queue = ensure_default_flow_queue(db)
        item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug="sports",
            prompt="Topic",
            status="queued",
        )
        db.add(item)
        db.flush()
        snapshot = get_or_create_topic_queue_snapshot(db, flow_queue_id=queue.id)
        assert snapshot is not None
        run = FactoryRun(
            run_id="run-perf-svc",
            topic_slug="sports",
            flow_path=rel_path,
            flow_version_id=version.id,
            topic_queue_snapshot_id=snapshot.id,
            selected_model="model-z",
            status="completed",
            manifest={"stats": {"total_tokens": 100}},
        )
        db.add(run)
        db.commit()

        records = [
            {"step_key": writer_key, "content": "draft"},
            {"step_key": review_key, "content": "Nice.\nVERDICT: ACCEPT"},
        ]
        apply_run_performance(db, run, records)
        assert run.first_pass_accept is True

        agg = aggregate_performance(
            db,
            flow_path=rel_path,
            flow_version_id=version.id,
            topic_queue_snapshot_id=snapshot.id,
            selected_model="model-z",
        )
        assert agg["overall"]["completed_count"] == 1
        assert agg["by_version"][0]["flow_version_id"] == version.id
        assert agg["by_topic_queue"][0]["queue_name"] is not None
        assert agg["by_model"][0]["model"] == "model-z"
        assert agg["runs"][0]["run_id"] == "run-perf-svc"

        queues = list_topic_queues_for_flow(db, rel_path)
        assert queues[0]["queue_slug"] is not None
    finally:
        db.close()


def test_apply_run_performance_on_bad_flow(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.flow_performance import apply_run_performance

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-bad-flow",
            topic_slug="sports",
            flow_path="missing/flow.flow.json",
            status="completed",
        )
        db.add(run)
        db.commit()
        apply_run_performance(db, run, [])
        assert run.first_pass_accept is None
    finally:
        db.close()


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
    assert body["overall"]["first_pass_yield_rate"] == 1.0
    assert body["overall"]["first_pass_completed_rate"] == 1.0

    analyze = client.post(
        "/api/flows/analyze",
        headers=api_headers,
        json={"path": rel_path},
    )
    assert analyze.status_code == 200
    assert analyze.json()["analysis"]["summary"]
