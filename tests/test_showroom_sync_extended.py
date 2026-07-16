from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from article_factory.services.showroom_status_sync import (
    ShowroomStatusLoop,
    push_showroom_factory_status,
    refresh_showroom_status,
    schedule_showroom_status_refresh,
    showroom_status_tick,
    sync_showroom_when_factory_busy,
)


@pytest.mark.asyncio
async def test_push_showroom_factory_status_with_runs(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.models import FactoryRun

    db = SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-live", topic_slug="sports", status="running"))
        db.commit()
        cms = AsyncMock()
        cms.put_factory_status = AsyncMock()
        await push_showroom_factory_status(db, cms)
        payload = cms.put_factory_status.await_args.args[0]
        assert payload["state"] == "running"
        assert payload["topic_slug"] == "sports"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_refresh_showroom_status_success(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
    finally:
        db.close()

    push_mock = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        push_mock,
    )

    assert await refresh_showroom_status() is True
    push_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_showroom_status_skips_without_cms(configured_db) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(db, {"cms_url": "", "cms_api_key": ""})
    finally:
        db.close()

    assert await refresh_showroom_status() is False


@pytest.mark.asyncio
async def test_refresh_showroom_status_retries_db_lock(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
    finally:
        db.close()

    calls = {"n": 0}

    async def flaky_push(db, cms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        flaky_push,
    )
    monkeypatch.setattr("article_factory.services.showroom_status_sync.time.sleep", lambda _s: None)

    assert await refresh_showroom_status(max_attempts=2) is True


@pytest.mark.asyncio
async def test_refresh_showroom_status_generic_failure(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
    finally:
        db.close()

    async def boom(db, cms):
        raise RuntimeError("cms down")

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        boom,
    )

    assert await refresh_showroom_status() is False


@pytest.mark.asyncio
async def test_schedule_showroom_status_refresh_debounced(monkeypatch) -> None:
    import article_factory.services.showroom_status_sync as sync_module

    sync_module._last_push_at = 1000.0
    request_mock = MagicMock()
    monkeypatch.setattr(sync_module.showroom_status_loop, "request_refresh", request_mock)

    with patch("article_factory.services.showroom_status_sync.time.monotonic", return_value=1001.0):
        schedule_showroom_status_refresh()
    request_mock.assert_called_once()


@pytest.mark.asyncio
async def test_schedule_showroom_status_refresh_force_in_loop(monkeypatch) -> None:
    refresh_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        refresh_mock,
    )

    async def run_schedule():
        schedule_showroom_status_refresh(force=True)
        await asyncio.sleep(0.01)

    await run_schedule()
    refresh_mock.assert_awaited()


@pytest.mark.asyncio
async def test_sync_showroom_when_factory_busy() -> None:
    with patch(
        "article_factory.services.showroom_status_sync.schedule_showroom_status_refresh"
    ) as schedule:
        await sync_showroom_when_factory_busy(active_run_count=0)
        schedule.assert_not_called()
        await sync_showroom_when_factory_busy(active_run_count=2)
        schedule.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_showroom_status_tick_operational_error(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(
            db,
            {"cms_url": "http://cms.test:8200", "cms_api_key": "secret"},
        )
    finally:
        db.close()

    refresh_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        refresh_mock,
    )

    async def locked_push(db, cms):
        raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        locked_push,
    )

    db = SessionLocal()
    try:
        await showroom_status_tick(db)
    finally:
        db.close()

    refresh_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_showroom_status_loop_lifecycle(monkeypatch) -> None:
    loop = ShowroomStatusLoop()
    assert loop.is_alive is False

    refresh_mock = AsyncMock(return_value=True)
    stop_after = {"n": 0}

    async def stop_refresh():
        stop_after["n"] += 1
        loop._running = False
        return True

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        stop_refresh,
    )
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.settings.heartbeat_interval_seconds",
        0.01,
    )

    await loop.start()
    assert loop.is_alive is True
    await asyncio.sleep(0.05)
    await loop.stop()
    assert loop.is_alive is False

    await loop.ensure_running()
    await loop.stop()
