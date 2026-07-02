from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from article_factory.app import create_app
from article_factory.config import settings
from article_factory.db import configure_engine, init_db
import article_factory.db as db_module
from article_factory.services.flow_storage import ensure_default_flows
from article_factory.services.factory_api_key_cache import invalidate_factory_api_key_cache


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'factory.db'}"


@pytest.fixture
def configured_db(db_url, tmp_path, monkeypatch) -> str:
    invalidate_factory_api_key_cache()
    flows_path = tmp_path / "flows"
    runs_path = tmp_path / "runs"
    flows_path.mkdir()
    runs_path.mkdir()
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("FLOWS_ROOT", str(flows_path))
    monkeypatch.setenv("FLOW_RUN_OUTPUTS_ROOT", str(runs_path))
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "flows_root", str(flows_path))
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(runs_path))
    monkeypatch.setattr(settings, "factory_api_key", "")
    monkeypatch.setattr(settings, "cms_api_key", "")
    configure_engine(db_url)
    init_db()
    db = db_module.SessionLocal()
    try:
        ensure_default_flows()
    finally:
        db.close()
    return db_url


@pytest.fixture
def client(configured_db, monkeypatch) -> TestClient:
    async def noop_start() -> None:
        return None

    async def noop_stop() -> None:
        return None

    from article_factory.orchestrator.runner import factory_loop

    monkeypatch.setattr(factory_loop, "start", noop_start)
    monkeypatch.setattr(factory_loop, "stop", noop_stop)
    monkeypatch.setattr(factory_loop, "_running", True)

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def api_headers(configured_db) -> dict[str, str]:
    from article_factory.services.runtime_settings import set_factory_api_key

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "test-factory-key")
    finally:
        db.close()
    return {"X-API-Key": "test-factory-key"}
