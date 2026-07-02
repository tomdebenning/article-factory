from __future__ import annotations


def test_auth_status_unconfigured(client) -> None:
    response = client.get("/api/auth")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["masked"] is None


def test_generate_auth_key_bootstrap(client) -> None:
    response = client.post("/api/auth/generate")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert len(body["api_key"]) > 20

    status = client.get("/api/auth")
    assert status.json()["configured"] is True


def test_generate_auth_key_requires_existing_key(client, api_headers) -> None:
    denied = client.post("/api/auth/generate")
    assert denied.status_code == 401

    allowed = client.post("/api/auth/generate", headers=api_headers)
    assert allowed.status_code == 200
