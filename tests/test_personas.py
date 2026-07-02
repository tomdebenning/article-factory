from __future__ import annotations


def test_persona_crud(client, api_headers) -> None:
    created = client.post(
        "/api/personas",
        headers=api_headers,
        json={
            "name": "Sports reporter",
            "description": "Energetic beat coverage",
            "style_prompt": "Write with short paragraphs and active voice.",
        },
    )
    assert created.status_code == 200
    persona = created.json()["persona"]
    assert persona["slug"] == "sports-reporter"
    assert persona["name"] == "Sports reporter"

    listing = client.get("/api/personas", headers=api_headers)
    assert listing.status_code == 200
    assert len(listing.json()["personas"]) == 1

    updated = client.put(
        "/api/personas/sports-reporter",
        headers=api_headers,
        json={
            "name": "Sports reporter (updated)",
            "description": "Updated description",
            "style_prompt": "Use vivid verbs and keep sentences tight.",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["persona"]["name"] == "Sports reporter (updated)"

    deleted = client.delete("/api/personas/sports-reporter", headers=api_headers)
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True

    missing = client.get("/api/personas/sports-reporter", headers=api_headers)
    assert missing.status_code == 404


def test_persona_requires_style_prompt(client, api_headers) -> None:
    response = client.post(
        "/api/personas",
        headers=api_headers,
        json={"name": "Empty style", "style_prompt": "   "},
    )
    assert response.status_code == 400
