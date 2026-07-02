from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from article_factory.services.control_plane_heartbeat import (
    control_plane_heartbeat_tick,
    effective_gateway_id,
    send_control_plane_heartbeats,
)
from article_factory.workers.executor import worker_agent_id


@pytest.mark.asyncio
async def test_send_control_plane_heartbeats_idle(configured_db) -> None:
    from article_factory.db import SessionLocal

    cp = AsyncMock()
    cp.post_node_heartbeat = AsyncMock()
    cp.post_agent_heartbeat = AsyncMock()

    db = SessionLocal()
    try:
        await send_control_plane_heartbeats(
            cp,
            db=db,
            active_run=None,
            gateway_id="factory-test",
            gateway_display_name="Article Factory",
        )
    finally:
        db.close()

    cp.post_node_heartbeat.assert_awaited_once()
    node_payload = cp.post_node_heartbeat.await_args.args[0]
    assert node_payload["node_id"] == "factory-test"
    assert node_payload["status"] == "idle"
    assert node_payload["descriptive_info"]["display_name"] == "Article Factory"
    assert cp.post_agent_heartbeat.await_count >= 1
    for call in cp.post_agent_heartbeat.await_args_list:
        assert call.args[0]["status"] == "idle"


@pytest.mark.asyncio
async def test_send_control_plane_heartbeats_busy_run(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun

    db = SessionLocal()
    try:
        run = FactoryRun(
            run_id="run-busy",
            topic_slug="sports",
            status="running",
            current_step="writer",
            selected_puller="puller-01",
            selected_model="llama3",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
    finally:
        db.close()

    cp = AsyncMock()
    cp.post_node_heartbeat = AsyncMock()
    cp.post_agent_heartbeat = AsyncMock()

    db = SessionLocal()
    try:
        await send_control_plane_heartbeats(
            cp,
            db=db,
            active_run=run,
            gateway_id="factory-test",
            gateway_display_name="Custom Factory Name",
        )
    finally:
        db.close()

    node_payload = cp.post_node_heartbeat.await_args.args[0]
    assert node_payload["status"] == "busy"
    assert node_payload["running_agent_count"] == 1

    writer_call = next(
        c
        for c in cp.post_agent_heartbeat.await_args_list
        if c.args[0]["agent_id"] == worker_agent_id("writer")
    )
    assert writer_call.args[0]["status"] == "waiting_for_llm"
    assert writer_call.args[0]["descriptive_info"]["run_id"] == "run-busy"


@pytest.mark.asyncio
async def test_control_plane_heartbeat_tick_uses_persisted_identity(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.factory_identity import save_factory_display_name
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(db, {"control_plane_url": "http://cp.test:8000"})
        save_factory_display_name(db, "Persisted Factory")
    finally:
        db.close()

    cp_mock = AsyncMock()
    cp_mock.post_node_heartbeat = AsyncMock()
    cp_mock.post_agent_heartbeat = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.ControlPlaneClient",
        lambda **kwargs: cp_mock,
    )

    db = SessionLocal()
    try:
        await control_plane_heartbeat_tick(db)
    finally:
        db.close()

    node_payload = cp_mock.post_node_heartbeat.await_args.args[0]
    assert node_payload["descriptive_info"]["display_name"] == "Persisted Factory"
    assert node_payload["node_id"].startswith("factory-")


@pytest.mark.asyncio
async def test_control_plane_heartbeat_tick_only_hits_control_plane(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret",
            },
        )
    finally:
        db.close()

    cp_mock = AsyncMock()
    cp_mock.post_node_heartbeat = AsyncMock()
    cp_mock.post_agent_heartbeat = AsyncMock()
    status_mock = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.ControlPlaneClient",
        lambda **kwargs: cp_mock,
    )
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_factory_status",
        status_mock,
    )

    db = SessionLocal()
    try:
        await control_plane_heartbeat_tick(db)
    finally:
        db.close()

    cp_mock.post_node_heartbeat.assert_awaited_once()
    status_mock.assert_not_awaited()


def test_effective_gateway_id_uses_hostname_when_unset(monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.control_plane_heartbeat.settings.gateway_id", "")
    monkeypatch.setattr("socket.gethostname", lambda: "dale.local")
    assert effective_gateway_id() == "factory-dale"


def test_effective_gateway_id_respects_config(monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.control_plane_heartbeat.settings.gateway_id", "factory-custom")
    assert effective_gateway_id() == "factory-custom"


@pytest.mark.asyncio
async def test_control_plane_client_heartbeats() -> None:
    from article_factory.control_plane.client import ControlPlaneClient

    client = ControlPlaneClient(base_url="http://cp.test")
    ok_response = MagicMock()
    ok_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=ok_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        await client.post_node_heartbeat({"node_id": "factory-test", "status": "idle"})
        await client.post_agent_heartbeat({"agent_id": "factory-worker-writer", "status": "idle"})

    assert mock_http.post.await_count == 2
