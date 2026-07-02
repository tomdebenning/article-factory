from __future__ import annotations

from article_factory.models import CompletedArticle, FactoryRun
from article_factory.services.flow_defaults import build_standard_sports_flow


def test_default_flow_step_order() -> None:
    flow = build_standard_sports_flow()
    keys = [step.step_key for step in sorted(flow.steps, key=lambda item: item.order)]
    assert keys == ["writer", "fact_asserter", "source_finder", "review"]


def test_enqueue_batch(client, api_headers) -> None:
    response = client.post(
        "/api/queue/batch",
        headers=api_headers,
        json={
            "topics": [
                "An article about Oklahoma State Football",
                "An article about University of Michigan football",
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2


def test_list_and_get_articles(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-article", topic_slug="sports", status="completed"))
        db.add(
            CompletedArticle(
                run_id="run-article",
                topic_slug="sports",
                title="Big Game",
                summary="Summary text",
                body_markdown="# Big Game\n\nBody",
            )
        )
        db.commit()
    finally:
        db.close()

    listing = client.get("/api/articles", headers=api_headers)
    assert listing.status_code == 200
    assert listing.json()["articles"][0]["title"] == "Big Game"

    detail = client.get("/api/articles/run-article", headers=api_headers)
    assert detail.status_code == 200
    assert "Body" in detail.json()["article"]["body_markdown"]
    assert detail.json()["article"]["has_content"] is True

    missing = client.get("/api/articles/missing", headers=api_headers)
    assert missing.status_code == 404


def test_factory_status_with_active_run(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun, TopicQueueItem

    db = db_module.SessionLocal()
    try:
        item = TopicQueueItem(topic_slug="sports", prompt="Active topic", status="running")
        db.add(item)
        db.flush()
        db.add(
            FactoryRun(
                run_id="run-active",
                topic_slug="sports",
                queue_item_id=item.id,
                status="running",
                current_step="writer",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/factory/status", headers=api_headers)
    body = response.json()
    assert body["state"] == "processing"
    assert body["active_run"]["topic_prompt"] == "Active topic"


def test_factory_status_counts(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import TopicQueueItem

    db = db_module.SessionLocal()
    try:
        db.add(TopicQueueItem(topic_slug="sports", prompt="Waiting", status="queued"))
        db.add(TopicQueueItem(topic_slug="sports", prompt="Done", status="completed"))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/factory/status", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert "readiness" in body
    assert body["readiness"]["phase"] in {"setup_required", "needs_topics", "ready", "processing"}
    assert body["queue_counts"]["queued"] >= 1
    assert body["queue_counts"]["completed"] >= 1
