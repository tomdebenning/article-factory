from __future__ import annotations

from unittest.mock import AsyncMock, patch

import article_factory.db as db_module
from article_factory.services.runtime_settings import (
    get_or_create_factory_settings,
    load_runtime_settings,
    update_factory_settings,
)


def test_normalize_base_url() -> None:
    from article_factory.services.runtime_settings import normalize_base_url

    assert normalize_base_url("sg02:8000") == "http://sg02:8000"
    assert normalize_base_url("http://sg02:8000/") == "http://sg02:8000"
    assert normalize_base_url("") == ""
    assert normalize_base_url("   ") == ""


def test_factory_settings_round_trip(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        row = get_or_create_factory_settings(db)
        db.commit()
        assert row.control_plane_url

        update_factory_settings(
            db,
            {
                "control_plane_url": "http://cp.test:8000",
                "cms_url": "http://cms.test:8200",
                "cms_api_key": "secret-key",
                "default_puller": "puller-a",
                "default_model": "model-a",
            },
        )
        update_factory_settings(db, {"default_puller": "puller-b"})
        runtime = load_runtime_settings(db)
        assert runtime.control_plane_url == "http://cp.test:8000"
        assert runtime.cms_url == "http://cms.test:8200"
        assert runtime.cms_api_key == "secret-key"
        assert runtime.default_puller == "puller-b"
        assert runtime.default_model == "model-a"
    finally:
        db.close()


def test_get_settings_requires_api_key(client, configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.services.runtime_settings import set_factory_api_key

    db = db_module.SessionLocal()
    try:
        set_factory_api_key(db, "required-secret")
    finally:
        db.close()

    response = client.get("/api/settings")
    assert response.status_code == 401


def test_get_and_put_settings_api(client, api_headers) -> None:
    response = client.get("/api/settings", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert "control_plane_url" in body

    updated = client.put(
        "/api/settings",
        headers=api_headers,
        json={
            "control_plane_url": "http://cp.local:8000",
            "cms_url": "http://cms.local:8200",
            "cms_api_key": "cms-key",
            "default_puller": "gpu-01",
            "default_model": "llama3",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["default_puller"] == "gpu-01"


def test_test_control_plane_failure(client, api_headers) -> None:
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=RuntimeError("down"))
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.routes.admin.httpx.AsyncClient", return_value=mock_http):
        response = client.post("/api/settings/test/control-plane", headers=api_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_test_cms_auth_failure(client, api_headers) -> None:
    health = AsyncMock()
    health.raise_for_status = lambda: None
    auth = AsyncMock()
    auth.status_code = 401

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=health)
    mock_http.put = AsyncMock(return_value=auth)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.routes.admin.httpx.AsyncClient", return_value=mock_http):
        response = client.post("/api/settings/test/cms", headers=api_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_test_cms_unreachable(client, api_headers) -> None:
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=RuntimeError("down"))
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.routes.admin.httpx.AsyncClient", return_value=mock_http):
        response = client.post("/api/settings/test/cms", headers=api_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_test_control_plane_endpoint(client, api_headers) -> None:
    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.routes.admin.httpx.AsyncClient", return_value=mock_http):
        response = client.post("/api/settings/test/control-plane", headers=api_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_cms_endpoint(client, api_headers) -> None:
    async def ok(*args, **kwargs):
        return True, "Connected to Showroom CMS at http://cms.test:8200"

    with patch("article_factory.routes.admin.check_cms_connection", ok):
        response = client.post("/api/settings/test/cms", headers=api_headers)

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_control_plane_with_body(client, api_headers) -> None:
    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.routes.admin.httpx.AsyncClient", return_value=mock_http):
        response = client.post(
            "/api/settings/test/control-plane",
            headers=api_headers,
            json={"control_plane_url": "sg02:8000", "cms_url": "http://cms", "cms_api_key": "k"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_cms_with_body(client, api_headers) -> None:
    async def ok(*args, **kwargs):
        return True, "Connected to Showroom CMS at http://cms.local:8200"

    with patch("article_factory.routes.admin.check_cms_connection", ok):
        response = client.post(
            "/api/settings/test/cms",
            headers=api_headers,
            json={"control_plane_url": "http://cp", "cms_url": "cms.local:8200", "cms_api_key": "k"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
