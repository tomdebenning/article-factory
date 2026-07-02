from __future__ import annotations

import article_factory.db as db_module
from article_factory.models import FactoryRun, StepExecution, TopicQueueItem
from article_factory.services.factory_stats import build_factory_stats, summarize_durations


def _seed_stats(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        item_a = TopicQueueItem(
            topic_slug="sports",
            prompt="An article about Michigan football",
            status="completed",
        )
        item_b = TopicQueueItem(
            topic_slug="sports",
            prompt="An article about Ohio State football",
            status="completed",
        )
        db.add_all([item_a, item_b])
        db.flush()

        run_a = FactoryRun(
            run_id="run-stats-a",
            topic_slug="sports",
            queue_item_id=item_a.id,
            status="completed",
            selected_puller="gpu-01",
            selected_model="llama3",
        )
        run_b = FactoryRun(
            run_id="run-stats-b",
            topic_slug="sports",
            queue_item_id=item_b.id,
            status="completed",
            selected_puller="gpu-02",
            selected_model="qwen3",
        )
        db.add_all([run_a, run_b])
        db.flush()

        db.add_all(
            [
                StepExecution(
                    run_id="run-stats-a",
                    step_key="writer",
                    status="completed",
                    puller="gpu-01",
                    model="llama3",
                    duration_ms=1000,
                    turns=1,
                ),
                StepExecution(
                    run_id="run-stats-a",
                    step_key="editor",
                    status="completed",
                    puller="gpu-01",
                    model="llama3",
                    duration_ms=3000,
                    turns=2,
                ),
                StepExecution(
                    run_id="run-stats-b",
                    step_key="writer",
                    status="completed",
                    puller="gpu-02",
                    model="qwen3",
                    duration_ms=2000,
                    turns=1,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_summarize_durations_empty() -> None:
    assert summarize_durations([]) == {
        "count": 0,
        "total_duration_ms": 0,
        "avg_duration_ms": 0,
        "median_duration_ms": 0,
    }


def test_summarize_durations_median() -> None:
    stats = summarize_durations([1000, 3000, 2000])
    assert stats["count"] == 3
    assert stats["total_duration_ms"] == 6000
    assert stats["avg_duration_ms"] == 2000
    assert stats["median_duration_ms"] == 2000


def test_build_factory_stats(configured_db) -> None:
    _seed_stats(configured_db)
    db = db_module.SessionLocal()
    try:
        payload = build_factory_stats(db)
    finally:
        db.close()

    assert payload["summary"]["count"] == 3
    assert payload["summary"]["total_duration_ms"] == 6000
    assert len(payload["by_puller"]) == 2
    assert len(payload["by_model"]) == 2
    assert len(payload["by_step"]) == 2

    puller_gpu_01 = next(row for row in payload["by_puller"] if row["puller"] == "gpu-01")
    assert puller_gpu_01["count"] == 2
    assert puller_gpu_01["total_duration_ms"] == 4000

    writer_rows = [row for row in payload["by_puller_step"] if row["step_key"] == "writer"]
    assert len(writer_rows) == 2

    recent = payload["recent_steps"]
    assert len(recent) == 3
    prompts = {row["prompt"] for row in recent}
    assert "An article about Michigan football" in prompts
    assert "An article about Ohio State football" in prompts


def test_factory_stats_api(client, api_headers, configured_db) -> None:
    _seed_stats(configured_db)
    response = client.get("/api/stats", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["count"] == 3
    assert body["recent_steps"][0]["run_id"]
