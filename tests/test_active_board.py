from __future__ import annotations

from datetime import datetime, timezone

from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.services.active_board import build_active_overview
from article_factory.services.flow_queues import create_flow_queue
from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_storage import write_flow


def _dt(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def test_build_active_overview_groups_running_and_history(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        queue = create_flow_queue(
            db,
            name="Sports",
            flow_path="sports/standard-4-step.flow.json",
            topic_slug="sports",
        )
        queued_item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug="sports",
            prompt="Queued topic",
            status="queued",
        )
        running_item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug="sports",
            prompt="Running topic",
            status="running",
        )
        db.add(queued_item)
        db.add(running_item)
        db.flush()

        db.add(
            FactoryRun(
                run_id="run-active",
                topic_slug="sports",
                flow_path=queue.flow_path,
                queue_item_id=running_item.id,
                status="running",
                current_step="writer",
                selected_model="llama3",
                started_at=_dt(2026, 5, 21, 14),
            )
        )
        db.add(
            FactoryRun(
                run_id="run-done",
                topic_slug="sports",
                flow_path=queue.flow_path,
                queue_item_id=queued_item.id,
                status="completed",
                selected_model="llama3",
                started_at=_dt(2026, 5, 20, 8),
                finished_at=_dt(2026, 5, 20, 9),
            )
        )
        db.commit()

        overview = build_active_overview(db)
    finally:
        db.close()

    assert len(overview["running_groups"]) == 1
    group = overview["running_groups"][0]
    assert group["queue_name"] == "Sports"
    assert group["model"] == "llama3"
    assert group["running_count"] == 1
    assert group["queued_count"] == 1
    assert len(group["runs"]) == 1
    assert group["runs"][0]["run_id"] == "run-active"
    assert group["runs"][0]["topic_prompt"] == "Running topic"
    assert group["runs"][0]["flow_queue_name"] == "Sports"
    assert len(group["runs"][0]["flow_steps"]) == 4

    assert len(overview["history_runs"]) == 1
    assert overview["history_runs"][0]["run_id"] == "run-done"
    assert overview["history_runs"][0]["finished_at"] is not None


def test_active_overview_endpoint(client, api_headers, configured_db) -> None:
    from article_factory.db import SessionLocal

    write_flow(
        "test/SimpleTest.flow.json",
        new_flow_definition(slug="SimpleTest", display_name="SimpleTest", step_count=1),
    )

    db = SessionLocal()
    try:
        queue = create_flow_queue(
            db,
            name="News",
            flow_path="test/SimpleTest.flow.json",
            topic_slug="general",
        )
        item = TopicQueueItem(
            flow_queue_id=queue.id,
            topic_slug="general",
            prompt="Evening run",
            status="completed",
        )
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-history",
                topic_slug="general",
                flow_path=queue.flow_path,
                queue_item_id=item.id,
                status="failed",
                selected_model="gpt-4",
                error="timeout",
                started_at=_dt(2026, 5, 19, 19),
                finished_at=_dt(2026, 5, 19, 20),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/active/overview", headers=api_headers)
    assert response.status_code == 200
    payload = response.json()
    assert "running_groups" in payload
    assert "history_runs" in payload
    assert payload["history_runs"][0]["run_id"] == "run-history"
    assert payload["history_runs"][0]["flow_queue_name"] == "News"
    assert payload["history_runs"][0]["flow_steps"][0]["step_key"] == "step_1"
