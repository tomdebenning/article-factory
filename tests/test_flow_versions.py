from __future__ import annotations

from article_factory.services.flow_schema import FlowStepCompletion, new_flow_step
from article_factory.services.flow_storage import create_flow, read_flow, write_flow
from article_factory.services.flow_versions import (
    diff_flow_versions,
    ensure_flow_version_for_run,
    get_flow_version,
    version_to_dict,
)


def test_ensure_flow_version_for_run_creates_when_missing(configured_db) -> None:
    import article_factory.db as db_module

    rel_path, _flow = create_flow(folder="", slug="version-run", display_name="Version Run", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = ensure_flow_version_for_run(db, rel_path)
        assert version.message == "Auto-created on first run"
        assert version.version_number == 1
        again = ensure_flow_version_for_run(db, rel_path)
        assert again.id == version.id
    finally:
        db.close()


def test_diff_flow_versions_added_removed_modified() -> None:
    previous = {
        "steps": [
            {"step_key": "writer", "label": "Writer", "system_prompt": "old", "user_prompt_template": "a"},
            {"step_key": "review", "label": "Review", "system_prompt": "r", "user_prompt_template": "b"},
        ]
    }
    current = {
        "steps": [
            {"step_key": "writer", "label": "Writer", "system_prompt": "new", "user_prompt_template": "a"},
            {"step_key": "editor", "label": "Editor", "system_prompt": "e", "user_prompt_template": "c"},
        ]
    }
    changes = diff_flow_versions(previous, current)
    assert {"step_key": "editor", "change": "added"} == {
        "step_key": changes[0]["step_key"],
        "change": changes[0]["change"],
    }
    assert any(item["change"] == "removed" and item["step_key"] == "review" for item in changes)
    assert any(
        item["change"] == "modified" and item["step_key"] == "writer" and item["field"] == "system_prompt"
        for item in changes
    )


def test_get_flow_version_and_version_to_dict(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.services.flow_versions import create_flow_version

    rel_path, _flow = create_flow(folder="", slug="version-dict", display_name="Version Dict", step_count=1)
    db = db_module.SessionLocal()
    try:
        row = create_flow_version(db, rel_path, message="baseline")
        loaded = get_flow_version(db, row.id)
        assert loaded is not None
        payload = version_to_dict(loaded)
        assert payload["display_name"] == "Version Dict"
        assert payload["step_count"] == 1
        assert get_flow_version(db, 999_999) is None
    finally:
        db.close()


def test_flow_versions_api(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.services.flow_versions import create_flow_version

    rel_path, flow = create_flow(folder="", slug="version-api", display_name="Version API", step_count=1)
    flow.steps[0].system_prompt = "v1"
    write_flow(rel_path, flow)
    db = db_module.SessionLocal()
    try:
        create_flow_version(db, rel_path, message="v1")
        flow.steps[0].system_prompt = "v2"
        write_flow(rel_path, flow)
        create_flow_version(db, rel_path, message="v2")
        db.commit()
    finally:
        db.close()

    missing = client.post("/api/flows/versions", headers=api_headers, json={"path": "missing.flow.json"})
    assert missing.status_code == 404

    versions = client.get(f"/api/flows/versions?path={rel_path}", headers=api_headers)
    assert versions.status_code == 200
    body = versions.json()["versions"]
    assert len(body) == 2
    assert body[0]["changes_from_previous"]

    detail = client.get(f"/api/flows/versions/detail?version_id={body[0]['id']}", headers=api_headers)
    assert detail.status_code == 200
    assert detail.json()["version"]["flow_content"]["steps"]

    missing_detail = client.get("/api/flows/versions/detail?version_id=999999", headers=api_headers)
    assert missing_detail.status_code == 404

    topic_queues = client.get(f"/api/flows/topic-queues?path={rel_path}", headers=api_headers)
    assert topic_queues.status_code == 200
    assert "topic_queues" in topic_queues.json()
