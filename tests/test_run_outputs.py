from __future__ import annotations

from article_factory.services.run_outputs import list_run_step_files, read_run_step_file


def test_list_and_read_run_step_files(configured_db, tmp_path, monkeypatch) -> None:
    from article_factory.config import settings

    runs_root = tmp_path / "runs"
    run_id = "run-step-files"
    steps_dir = runs_root / run_id / "steps"
    steps_dir.mkdir(parents=True)
    (steps_dir / "01-writer.md").write_text("# Draft\n\nBody", encoding="utf-8")

    monkeypatch.setattr(settings, "flow_run_outputs_root", str(runs_root))

    files = list_run_step_files(run_id)
    assert len(files) == 1
    assert files[0]["name"] == "01-writer.md"

    content = read_run_step_file(run_id, "01-writer.md")
    assert "Body" in content


def test_get_run_includes_step_files(client, api_headers, configured_db, tmp_path, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.config import settings
    from article_factory.models import FactoryRun

    runs_root = tmp_path / "runs"
    run_id = "run-with-files"
    steps_dir = runs_root / run_id / "steps"
    steps_dir.mkdir(parents=True)
    (steps_dir / "04-review.md").write_text("VERDICT: ACCEPT", encoding="utf-8")
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(runs_root))

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id=run_id, topic_slug="sports", status="completed"))
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/runs/{run_id}", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["step_files"][0]["name"] == "04-review.md"

    file_response = client.get(f"/api/runs/{run_id}/step-files/04-review.md", headers=api_headers)
    assert file_response.status_code == 200
    assert "ACCEPT" in file_response.json()["content"]
