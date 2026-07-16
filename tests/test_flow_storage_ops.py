from __future__ import annotations

import pytest

from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_storage import (
    create_flow,
    create_flow_from_template,
    create_folder,
    delete_flow,
    delete_folder,
    export_flow,
    import_flow,
    list_folder_flows,
    list_templates,
    list_tree,
    normalize_flow_rel_path,
    read_flow,
    write_flow,
)


def test_normalize_flow_rel_path_adds_suffix() -> None:
    assert normalize_flow_rel_path("sports/foo") == "sports/foo.flow.json"


def test_create_folder_list_and_delete(configured_db) -> None:
    created = create_folder("experiments/deep")
    assert created["path"] == "experiments/deep"

    listing = list_folder_flows("experiments/deep")
    assert listing == []

    with pytest.raises(FileExistsError):
        create_folder("experiments/deep")

    delete_folder("experiments/deep")
    with pytest.raises(FileNotFoundError):
        delete_folder("experiments/missing")


def test_delete_folder_rejects_nonempty_and_root(configured_db) -> None:
    create_folder("experiments/full")
    rel_path, _flow = create_flow(folder="experiments/full", slug="one", display_name="One", step_count=1)
    assert rel_path.startswith("experiments/full/")

    with pytest.raises(ValueError, match="not empty"):
        delete_folder("experiments/full")

    with pytest.raises(ValueError, match="root"):
        delete_folder("")


def test_path_escape_rejected() -> None:
    with pytest.raises(ValueError, match="escapes"):
        read_flow("../../outside.flow.json")


def test_import_export_and_delete_flow(configured_db) -> None:
    rel_path, flow = create_flow(folder="imports", slug="export-me", display_name="Export Me", step_count=2)
    payload = export_flow(rel_path)
    assert payload["flow"]["display_name"] == "Export Me"

    delete_flow(rel_path)
    with pytest.raises(FileNotFoundError):
        export_flow(rel_path)

    imported_path = import_flow(
        read_flow("sports/standard-4-step.flow.json"),
        folder="imports",
        slug="reimported",
    )
    assert imported_path == "imports/reimported.flow.json"
    assert read_flow(imported_path).slug == "reimported"

    with pytest.raises(FileExistsError):
        import_flow(read_flow(imported_path), folder="imports", slug="reimported")


def test_create_from_template_and_list_templates(configured_db) -> None:
    templates = list_templates()
    assert templates
    template_path = next(item["path"] for item in templates if item["slug"] == "single-writer")
    assert template_path.startswith("_templates/")
    rel_path, flow = create_flow_from_template(
        template_path=template_path,
        folder="from-template",
        slug="from-single",
        display_name="From Single",
    )
    assert rel_path == "from-template/from-single.flow.json"
    assert flow.display_name == "From Single"

    with pytest.raises(ValueError, match="_templates"):
        create_flow_from_template(
            template_path="missing.flow.json",
            folder="x",
            slug="x",
            display_name="X",
        )


def test_list_tree_and_folder_flows_errors(configured_db) -> None:
    tree = list_tree("")
    assert tree["type"] == "folder"

    with pytest.raises(FileNotFoundError):
        list_tree("missing-folder")

    with pytest.raises(NotADirectoryError):
        write_flow("flat-only.flow.json", new_flow_definition(slug="flat", display_name="Flat", step_count=1))
        list_tree("flat-only.flow.json")


def test_flow_storage_api_errors(client, api_headers, configured_db) -> None:
    missing = client.get("/api/flows/file", headers=api_headers, params={"path": "missing.flow.json"})
    assert missing.status_code == 404

    bad_tree = client.get("/api/flows/tree", headers=api_headers, params={"path": "missing-dir"})
    assert bad_tree.status_code in {400, 404}

    bad_list = client.get("/api/flows/list", headers=api_headers, params={"path": "missing-dir"})
    assert bad_list.status_code in {400, 404}

    dup = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "dup", "slug": "dup-flow", "display_name": "Dup", "step_count": 1},
    )
    assert dup.status_code == 200
    conflict = client.post(
        "/api/flows/create",
        headers=api_headers,
        json={"folder": "dup", "slug": "dup-flow", "display_name": "Dup", "step_count": 1},
    )
    assert conflict.status_code == 409

    folder = client.post("/api/flows/folders", headers=api_headers, json={"path": "dup-folder"})
    assert folder.status_code == 200
    folder_conflict = client.post("/api/flows/folders", headers=api_headers, json={"path": "dup-folder"})
    assert folder_conflict.status_code == 409

    delete_missing = client.delete("/api/flows/file", headers=api_headers, params={"path": "missing.flow.json"})
    assert delete_missing.status_code == 404

    remove_missing_folder = client.delete("/api/flows/folders", headers=api_headers, params={"path": "missing-folder"})
    assert remove_missing_folder.status_code == 404

    bad_import = client.post(
        "/api/flows/import",
        headers=api_headers,
        json={"folder": "bad", "slug": "bad", "flow": {"not": "a flow"}},
    )
    assert bad_import.status_code == 400

    bad_template = client.post(
        "/api/flows/from-template",
        headers=api_headers,
        json={
            "template_path": "missing.flow.json",
            "folder": "x",
            "slug": "x",
            "display_name": "X",
        },
    )
    assert bad_template.status_code in {400, 404}

    bad_duplicate = client.post(
        "/api/flows/duplicate",
        headers=api_headers,
        json={"path": "missing.flow.json"},
    )
    assert bad_duplicate.status_code == 404

    bad_export = client.get("/api/flows/export", headers=api_headers, params={"path": "missing.flow.json"})
    assert bad_export.status_code == 404
