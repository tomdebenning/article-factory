from __future__ import annotations

import article_factory.db as db_module
from article_factory.services.factory_identity import (
    load_factory_identity,
    save_factory_display_name,
)


def test_factory_identity_persists_display_name(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        first = load_factory_identity(db)
        assert first.gateway_id.startswith("factory-")
        assert first.gateway_display_name == "Article Factory"

        updated = save_factory_display_name(db, "Office Article Factory")
        assert updated.gateway_id == first.gateway_id
        assert updated.gateway_display_name == "Office Article Factory"

        reloaded = load_factory_identity(db)
        assert reloaded.gateway_id == first.gateway_id
        assert reloaded.gateway_display_name == "Office Article Factory"
    finally:
        db.close()


def test_put_gateway_identity_api(client, api_headers) -> None:
    response = client.put(
        "/api/settings/gateway-identity",
        headers=api_headers,
        json={"gateway_display_name": "Test Factory"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gateway_display_name"] == "Test Factory"
    assert body["gateway_id"].startswith("factory-")

    again = client.get("/api/settings", headers=api_headers)
    assert again.status_code == 200
    assert again.json()["gateway_display_name"] == "Test Factory"
