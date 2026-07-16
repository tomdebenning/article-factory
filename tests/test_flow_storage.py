from __future__ import annotations

import pytest

from article_factory.services.flow_schema import new_flow_definition
from article_factory.services.flow_storage import (
    create_flow,
    delete_flow,
    delete_folder,
    duplicate_flow,
    export_flow,
    flows_root,
    import_flow,
    is_flow_file,
    is_template_path,
    list_folder_flows,
    list_templates,
    list_tree,
    move_flow,
    normalize_flow_rel_path,
    read_flow,
    run_outputs_root,
    save_step_response_to_disk,
    write_flow,
)


def test_normalize_flow_rel_path() -> None:
    assert normalize_flow_rel_path("foo").endswith(".flow.json")
    assert normalize_flow_rel_path("bar.flow.json") == "bar.flow.json"


def test_is_flow_file_and_template_path(configured_db) -> None:
    flow_file = flows_root() / "sports" / "standard-4-step.flow.json"
    assert is_flow_file(flow_file) is True
    assert is_template_path("_templates/standard-4-step.flow.json") is True
    assert is_template_path("sports/foo.flow.json") is False


def test_flows_and_run_outputs_roots(configured_db) -> None:
    assert flows_root().is_dir()
    assert run_outputs_root().is_dir()


def test_create_duplicate_move_flow(configured_db) -> None:
    rel_path, _flow = create_flow(folder="unit", slug="alpha", display_name="Alpha", step_count=2)
    assert read_flow(rel_path).display_name == "Alpha"

    dup_path, _dup = duplicate_flow(rel_path, slug="alpha-copy")
    assert dup_path == "unit/alpha-copy.flow.json"

    moved_path, _moved = move_flow(rel_path, folder="unit", slug="alpha-moved")
    assert moved_path == "unit/alpha-moved.flow.json"
    with pytest.raises(FileNotFoundError):
        read_flow(rel_path)


def test_import_export_flow(configured_db) -> None:
    rel_path, flow = create_flow(folder="", slug="export-me", display_name="Export", step_count=1)
    payload = export_flow(rel_path)
    delete_flow(rel_path)

    imported_path = import_flow(flow, folder="imports", slug="export-me")
    assert imported_path == "imports/export-me.flow.json"
    assert export_flow(imported_path)["flow"]["slug"] == "export-me"
    assert payload["flow"]["display_name"] == "Export"


def test_list_tree_and_folder_flows(configured_db) -> None:
    create_flow(folder="catalog", slug="listed", display_name="Listed", step_count=1)
    tree = list_tree("catalog")
    assert tree["type"] == "folder"
    flows = list_folder_flows("catalog")
    assert any(item["slug"] == "listed" for item in flows)
    assert any(item["slug"] == "standard-4-step" for item in list_templates())


def test_save_step_response_to_disk(configured_db) -> None:
    path = save_step_response_to_disk(run_id="run-disk", step_order=1, step_key="writer", content="# Draft")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Draft"


def test_delete_folder_and_errors(configured_db) -> None:
    create_flow(folder="empty-dir", slug="temp", display_name="Temp", step_count=1)
    delete_flow("empty-dir/temp.flow.json")
    delete_folder("empty-dir")

    with pytest.raises(ValueError, match="Cannot delete flows root"):
        delete_folder("")

    rel_path, _ = create_flow(folder="blocked", slug="stay", display_name="Stay", step_count=1)
    with pytest.raises(ValueError, match="not empty"):
        delete_folder("blocked")

    with pytest.raises(ValueError, match="Cannot move flows into _templates"):
        move_flow(rel_path, folder="_templates", slug="nope")


def test_resolve_under_root_escape_blocked(configured_db) -> None:
    with pytest.raises(ValueError, match="escapes"):
        read_flow("../../outside.flow.json")


def test_write_flow_roundtrip(configured_db) -> None:
    flow = new_flow_definition(slug="roundtrip", display_name="Roundtrip", step_count=1)
    write_flow("roundtrip.flow.json", flow)
    loaded = read_flow("roundtrip.flow.json")
    assert loaded.slug == "roundtrip"
