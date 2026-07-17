from __future__ import annotations

import json

from article_factory.models import SavedQueue
from article_factory.services.queue_presets import (
    delete_queue_preset,
    list_queue_presets,
    migrate_file_presets_to_db,
    parse_topics_csv,
    parse_topics_lines,
    parse_topics_text,
    read_queue_preset,
    write_queue_preset,
)


def test_parse_topics_lines() -> None:
    assert parse_topics_lines("One\n\nTwo\n") == ["One", "Two"]


def test_parse_topics_csv() -> None:
    content = "Topic A,extra\nTopic B,more\n"
    assert parse_topics_csv(content) == ["Topic A", "Topic B"]


def test_parse_topics_text_by_extension() -> None:
    assert parse_topics_text("A\nB", filename="topics.txt") == ["A", "B"]
    assert parse_topics_text("A,x\nB,y", filename="topics.csv") == ["A", "B"]


def test_queue_preset_round_trip(configured_db) -> None:
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        saved = write_queue_preset(
            db,
            {
                "name": "Sports batch",
                "topic_slug": "sports",
                "flow_path": "sports/standard-4-step.flow.json",
                "default_model": "test-model",
                "topics": ["Topic one", "Topic two"],
            },
        )
        db.commit()
        assert saved["slug"] == "sports-batch"
        listing = list_queue_presets(db)
        assert len(listing) == 1
        loaded = read_queue_preset(db, "sports-batch")
        assert loaded["topics"] == ["Topic one", "Topic two"]
        deleted = delete_queue_preset(db, "sports-batch")
        db.commit()
        assert deleted["name"] == "Sports batch"
        assert list_queue_presets(db) == []
    finally:
        db.close()


def test_migrate_file_presets_to_db(configured_db, tmp_path, monkeypatch) -> None:
    from article_factory.db import SessionLocal

    presets_dir = tmp_path / "queue-presets"
    presets_dir.mkdir()
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: presets_dir)

    (presets_dir / "legacy.queue.json").write_text(
        json.dumps(
            {
                "name": "Legacy queue",
                "flow_path": "sports/standard-4-step.flow.json",
                "topic_slug": "sports",
                "default_model": "test-model",
                "topics": ["From disk"],
            }
        ),
        encoding="utf-8",
    )

    db = SessionLocal()
    try:
        assert migrate_file_presets_to_db(db) == 1
        db.commit()
        assert not (presets_dir / "legacy.queue.json").exists()
        rows = db.query(SavedQueue).all()
        assert len(rows) == 1
        assert rows[0].topics == ["From disk"]
    finally:
        db.close()


def test_save_shift_plan_stores_preset(client, api_headers, configured_db) -> None:
    from article_factory.services.runtime_settings import update_factory_settings
    from article_factory.services.shift_windows import today_and_tomorrow_shift_windows
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        update_factory_settings(
            db,
            {"control_plane_url": "http://cp.test:8000", "default_model": "test-model"},
        )
        db.commit()
    finally:
        db.close()

    window = today_and_tomorrow_shift_windows()[0]
    response = client.post(
        "/api/shifts/plans/save",
        headers=api_headers,
        json={
            "window_key": window.window_key,
            "default_model": "test-model",
            "desks": [
                {
                    "desk_path": "sports/standard-4-step.flow.json",
                    "topic_slug": "sports",
                    "name": "Launch queue",
                }
            ],
            "assignments_by_desk_index": {"0": ["Topic A", "Topic B"]},
            "save_preset": True,
            "preset_name": "Launch queue",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["assignment_total"] == 2
    assert body["preset"]["slug"] == "launch-queue"

    presets = client.get("/api/flow-queues/presets", headers=api_headers)
    assert presets.status_code == 200
    assert any(item["slug"] == "launch-queue" for item in presets.json()["presets"])

    loaded = client.get("/api/flow-queues/presets/launch-queue", headers=api_headers)
    assert loaded.status_code == 200
    assert loaded.json()["preset"]["topics"] == ["Topic A", "Topic B"]

    db = db_module.SessionLocal()
    try:
        assert db.query(SavedQueue).filter_by(slug="launch-queue").count() == 1
    finally:
        db.close()
