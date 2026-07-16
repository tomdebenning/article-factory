from __future__ import annotations

from article_factory.models import FactoryRun, StepExecution, TopicQueueSnapshot
from article_factory.services.batch_comparison import build_batch_comparison


def test_build_batch_comparison(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    rel_path, _flow = create_flow(folder="", slug="batch-test", display_name="Batch Test", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        snapshot = TopicQueueSnapshot(
            queue_slug="test-q",
            queue_name="Test Queue",
            content_hash="abc123",
            topics=[
                {"id": 10, "topic_slug": "sports", "prompt": "Write about baseball", "status": "queued"},
                {"id": 11, "topic_slug": "tech", "prompt": "Write about AI", "status": "queued"},
            ],
            topic_count=2,
        )
        db.add(snapshot)
        db.flush()

        db.add(
            FactoryRun(
                run_id="batch-run-1",
                topic_slug="sports",
                flow_path=rel_path,
                queue_item_id=10,
                status="completed",
                flow_version_id=version.id,
                topic_queue_snapshot_id=snapshot.id,
                selected_model="model-a",
                first_pass_accept=True,
                review_round=1,
                manifest={
                    "production": {"review_round": 1, "iteration_count": 1},
                    "step_stats": [
                        {"step_key": "writer", "turns": 2},
                        {"step_key": "review", "turns": 1, "content": "VERDICT: ACCEPT"},
                    ],
                },
            )
        )
        db.add(
            FactoryRun(
                run_id="batch-run-2",
                topic_slug="tech",
                flow_path=rel_path,
                queue_item_id=11,
                status="failed",
                flow_version_id=version.id,
                topic_queue_snapshot_id=snapshot.id,
                selected_model="model-a",
                error="Max flow iterations exceeded",
            )
        )
        db.add(
            StepExecution(
                run_id="batch-run-1",
                step_key="writer",
                status="completed",
                turns=2,
            )
        )
        db.commit()

        result = build_batch_comparison(db, topic_queue_snapshot_id=snapshot.id)
        assert result["summary"]["run_count"] == 2
        assert result["summary"]["failure_count"] == 1
        assert result["summary"]["first_pass_count"] == 1
        assert len(result["topics"]) == 2
        assert result["topics"][0]["run_id"] == "batch-run-1"
        assert result["topics"][1]["error_group"] == "iteration_limit"

        groups = {row["error_group"]: row["count"] for row in result["error_groups"]}
        assert groups["completed"] == 1
        assert groups["iteration_limit"] == 1
    finally:
        db.close()


def test_batch_comparison_api(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.services.flow_storage import create_flow

    rel_path, _flow = create_flow(folder="", slug="batch-api", display_name="Batch API", step_count=2)
    db = db_module.SessionLocal()
    try:
        snapshot = TopicQueueSnapshot(
            queue_slug="api-q",
            queue_name="API Queue",
            content_hash="def456",
            topics=[{"id": 1, "topic_slug": "general", "prompt": "hello", "status": "queued"}],
            topic_count=1,
        )
        db.add(snapshot)
        db.flush()
        db.add(
            FactoryRun(
                run_id="batch-api-run",
                topic_slug="general",
                flow_path=rel_path,
                queue_item_id=1,
                status="completed",
                topic_queue_snapshot_id=snapshot.id,
                first_pass_accept=True,
            )
        )
        db.commit()
        snapshot_id = snapshot.id
    finally:
        db.close()

    response = client.get(
        f"/api/flows/batch-comparison?topic_queue_snapshot_id={snapshot_id}",
        headers=api_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["run_count"] == 1
    assert body["topics"][0]["run_id"] == "batch-api-run"

    tag = client.put(
        "/api/flows/runs/batch-api-run/error-tag",
        headers=api_headers,
        json={"error_group": "completed", "note": "looks good"},
    )
    assert tag.status_code == 200
    assert tag.json()["error_tag"]["note"] == "looks good"

    perf = client.get(f"/api/flows/performance?path={rel_path}", headers=api_headers)
    assert perf.status_code == 200
    assert perf.json()["batches"]
