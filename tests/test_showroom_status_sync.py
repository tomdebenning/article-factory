from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from article_factory.services.showroom_status_sync import showroom_status_tick


@pytest.mark.asyncio
async def test_showroom_status_tick_only_hits_cms(configured_db, monkeypatch) -> None:
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

    status_mock = AsyncMock()
    cp_mock = AsyncMock()
    cp_mock.post_node_heartbeat = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_factory_status",
        status_mock,
    )
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.CmsClient",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "article_factory.services.control_plane_heartbeat.ControlPlaneClient",
        lambda **kwargs: cp_mock,
    )

    db = SessionLocal()
    try:
        await showroom_status_tick(db)
    finally:
        db.close()

    status_mock.assert_awaited_once()
    cp_mock.post_node_heartbeat.assert_not_awaited()


@pytest.mark.asyncio
async def test_showroom_status_tick_skips_without_cms_key(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal
    from article_factory.services.runtime_settings import update_factory_settings

    db = SessionLocal()
    try:
        update_factory_settings(db, {"cms_url": "http://cms.test:8200", "cms_api_key": ""})
    finally:
        db.close()

    status_mock = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_factory_status",
        status_mock,
    )

    db = SessionLocal()
    try:
        await showroom_status_tick(db)
    finally:
        db.close()

    status_mock.assert_not_awaited()
