from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from article_factory.config import settings


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None

# Lightweight SQLite migrations for existing installs (create_all does not ALTER).
_SCHEMA_PATCHES: dict[str, list[tuple[str, str]]] = {
    "factory_settings": [
        ("factory_api_key", "VARCHAR(256) NOT NULL DEFAULT ''"),
        ("default_flow_path", "VARCHAR(256) NOT NULL DEFAULT 'sports/standard-4-step.flow.json'"),
        ("brave_search_api_key", "VARCHAR(256) NOT NULL DEFAULT ''"),
        ("gateway_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
        ("gateway_display_name", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ],
    "topic_queue": [
        ("flow_path", "VARCHAR(256) NOT NULL DEFAULT 'sports/standard-4-step.flow.json'"),
        ("flow_queue_id", "INTEGER"),
    ],
    "factory_runs": [
        ("selected_puller", "VARCHAR(128) NOT NULL DEFAULT ''"),
        ("selected_model", "VARCHAR(128) NOT NULL DEFAULT ''"),
        ("pipeline_state", "JSON"),
        ("flow_path", "VARCHAR(256) NOT NULL DEFAULT 'sports/standard-4-step.flow.json'"),
        ("flow_version_id", "INTEGER"),
        ("topic_queue_snapshot_id", "INTEGER"),
        ("first_pass_accept", "INTEGER"),
    ],
    "step_executions": [
        ("response_content", "TEXT"),
        ("duration_ms", "INTEGER"),
        ("usage", "JSON"),
        ("tools_used", "JSON"),
        ("progress", "JSON"),
        ("turns", "INTEGER"),
    ],
}


def _configure_sqlite_connection(dbapi_connection) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def configure_engine(url: str | None = None) -> None:
    global engine, SessionLocal
    db_url = url or settings.database_url
    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(db_url, connect_args=connect_args)
    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            _configure_sqlite_connection(dbapi_connection)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


configure_engine()


def migrate_schema() -> None:
    assert engine is not None
    if not str(engine.url).startswith("sqlite"):
        return

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _SCHEMA_PATCHES.items():
            if table not in existing_tables:
                continue
            existing_cols = {col["name"] for col in inspector.get_columns(table)}
            for col_name, col_def in columns:
                if col_name in existing_cols:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))


def init_db() -> None:
    from article_factory import models  # noqa: F401

    assert engine is not None
    Base.metadata.create_all(bind=engine)
    migrate_schema()


def get_db():
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
