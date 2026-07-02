from __future__ import annotations

import sqlite3

import article_factory.db as db_module
from article_factory.db import configure_engine, init_db, migrate_schema
from article_factory.models import FactoryRun


def test_migrate_schema_adds_missing_run_columns(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE factory_runs (
            id INTEGER PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            topic_slug VARCHAR(64) NOT NULL,
            queue_item_id INTEGER,
            status VARCHAR(32) NOT NULL,
            current_step VARCHAR(32),
            draft_number INTEGER NOT NULL DEFAULT 1,
            review_round INTEGER NOT NULL DEFAULT 0,
            manifest JSON,
            error TEXT,
            started_at DATETIME,
            finished_at DATETIME
        )
        """
    )
    conn.commit()
    conn.close()

    url = f"sqlite:///{db_path}"
    configure_engine(url)
    migrate_schema()

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(factory_runs)")}
    conn.close()
    assert "selected_puller" in columns
    assert "selected_model" in columns

    init_db()
    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-migrated", topic_slug="sports", status="running"))
        db.commit()
        run = db.query(FactoryRun).filter_by(run_id="run-migrated").one()
        assert run.selected_puller == ""
        assert run.selected_model == ""
    finally:
        db.close()


def test_migrate_schema_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE factory_runs (id INTEGER PRIMARY KEY, run_id VARCHAR(64) NOT NULL, topic_slug VARCHAR(64) NOT NULL)"
    )
    conn.commit()
    conn.close()

    url = f"sqlite:///{db_path}"
    configure_engine(url)
    migrate_schema()
    migrate_schema()
