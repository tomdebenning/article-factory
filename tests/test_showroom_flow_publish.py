from __future__ import annotations

import article_factory.db as db_module
from article_factory.services.showroom_flow_publish import batch_is_complete, build_flow_batch_payload


def test_batch_is_complete(configured_db) -> None:
    from article_factory.models import FactoryRun, TopicQueueSnapshot

    db = db_module.SessionLocal()
    snapshot = TopicQueueSnapshot(topics=[{"id": 1}], content_hash="abc")
    db.add(snapshot)
    db.flush()

    db.add(
        FactoryRun(
            run_id="run-1",
            topic_slug="general",
            status="completed",
            topic_queue_snapshot_id=snapshot.id,
        )
    )
    db.add(
        FactoryRun(
            run_id="run-2",
            topic_slug="general",
            status="running",
            topic_queue_snapshot_id=snapshot.id,
        )
    )
    db.commit()

    assert batch_is_complete(db, snapshot.id) is False

    run = db.query(FactoryRun).filter_by(run_id="run-2").one()
    run.status = "failed"
    db.commit()

    assert batch_is_complete(db, snapshot.id) is True
    db.close()


def test_build_flow_batch_payload(configured_db) -> None:
    from article_factory.models import FactoryRun, TopicQueueSnapshot

    db = db_module.SessionLocal()
    snapshot = TopicQueueSnapshot(
        topics=[{"id": 1, "topic_slug": "general", "prompt": "Hello world"}],
        content_hash="def",
        queue_name="Test batch",
    )
    db.add(snapshot)
    db.flush()

    db.add(
        FactoryRun(
            run_id="run-complete",
            topic_slug="general",
            flow_path="general/test.flow.json",
            flow_version_id=1,
            selected_model="qwen3:8b",
            selected_puller="ollama",
            status="completed",
            topic_queue_snapshot_id=snapshot.id,
            queue_item_id=1,
            manifest={"stats": {"total_tokens": 100}},
            first_pass_accept=True,
        )
    )
    db.commit()

    payload = build_flow_batch_payload(db, snapshot.id)
    assert payload["topic_queue_snapshot_id"] == snapshot.id
    assert payload["flow_path"] == "general/test.flow.json"
    assert payload["summary"]["run_count"] == 1
    assert payload["spreadsheet"]["headers"]
    assert payload["topics"]
    db.close()
