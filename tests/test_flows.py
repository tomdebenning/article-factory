from __future__ import annotations


def test_create_and_load_flow(client, api_headers) -> None:
    response = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={
            "folder": "experiments",
            "slug": "two-step",
            "display_name": "Two step",
            "step_count": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "experiments/two-step.flow.json"
    assert len(body["flow"]["steps"]) == 2

    tree = client.get("/api/flows/tree", headers=api_headers)
    assert tree.status_code == 200
    sports = next(child for child in tree.json()["children"] if child["name"] == "sports")
    assert any(entry["name"] == "standard-4-step.flow.json" for entry in sports["children"])
    experiments = next(child for child in tree.json()["children"] if child["name"] == "experiments")
    assert any(entry["name"] == "two-step.flow.json" for entry in experiments["children"])

    loaded = client.get(
        "/api/flows/file",
        headers=api_headers,
        params={"path": "experiments/two-step.flow.json"},
    )
    assert loaded.status_code == 200
    assert loaded.json()["flow"]["display_name"] == "Two step"

    dup = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "experiments/two-step.flow.json"},
    )
    assert dup.status_code == 200
    assert dup.json()["path"] == "experiments/two-step-copy.flow.json"


def test_flow_templates(client, api_headers) -> None:
    response = client.get("/api/flows/templates", headers=api_headers)
    assert response.status_code == 200
    templates = response.json()["templates"]
    assert any(item["slug"] == "standard-4-step" for item in templates)
    assert any(item["slug"] == "single-writer" for item in templates)

    template_path = next(item["path"] for item in templates if item["slug"] == "single-writer")
    created = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": template_path,
            "folder": "from-template",
            "slug": "my-single",
            "display_name": "My single writer",
        },
    )
    assert created.status_code == 200
    assert created.json()["path"] == "from-template/my-single.flow.json"
    assert created.json()["flow"]["display_name"] == "My single writer"


def test_flow_import_export(client, api_headers) -> None:
    exported = client.get(
        "/api/flows/export",
        headers=api_headers,
        params={"path": "sports/standard-4-step.flow.json"},
    )
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["path"] == "sports/standard-4-step.flow.json"
    assert payload["flow"]["slug"] == "standard-4-step"

    imported = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={
            "folder": "imports",
            "slug": "imported-flow",
            "flow": payload["flow"],
        },
    )
    assert imported.status_code == 200
    assert imported.json()["path"] == "imports/imported-flow.flow.json"

    conflict = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={
            "folder": "imports",
            "slug": "imported-flow",
            "flow": payload["flow"],
        },
    )
    assert conflict.status_code == 409

    overwrite = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={
            "folder": "imports",
            "slug": "imported-flow",
            "flow": payload["flow"],
            "overwrite": True,
        },
    )
    assert overwrite.status_code == 200


def test_flow_save_strips_model_and_puller(client, api_headers) -> None:
    created = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={
            "folder": "experiments",
            "slug": "model-strip-test",
            "display_name": "Model strip test",
            "step_count": 1,
        },
    )
    assert created.status_code == 200
    path = created.json()["path"]
    flow = created.json()["flow"]
    flow["steps"][0]["model"] = "legacy-model"
    flow["steps"][0]["puller"] = "legacy-puller"

    saved = client.put(
        "/api/flows/file",
        headers=api_headers,
        params={"path": path},
        json={"flow": flow},
    )
    assert saved.status_code == 200
    assert saved.json()["flow"]["steps"][0]["model"] == ""
    assert saved.json()["flow"]["steps"][0]["puller"] == ""


def test_flow_move_out_of_templates(client, api_headers) -> None:
    templates = client.get("/api/flows/templates", headers=api_headers).json()["templates"]
    template_path = next(item["path"] for item in templates if item["slug"] == "single-writer")
    moved = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": template_path, "folder": "promoted", "slug": "my-single-writer"},
    )
    assert moved.status_code == 200
    assert moved.json()["path"] == "promoted/my-single-writer.flow.json"
    assert moved.json()["moved_from"] == template_path

    missing = client.get(
        "/api/flows/file",
        headers=api_headers,
        params={"path": template_path},
    )
    assert missing.status_code == 404

    loaded = client.get(
        "/api/flows/file",
        headers=api_headers,
        params={"path": "promoted/my-single-writer.flow.json"},
    )
    assert loaded.status_code == 200

    into_templates = client.post(
        "/api/flows/move",
        headers=api_headers,
        json={"path": "promoted/my-single-writer.flow.json", "folder": "_templates", "slug": "nope"},
    )
    assert into_templates.status_code == 400
