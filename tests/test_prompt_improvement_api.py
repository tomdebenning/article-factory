from __future__ import annotations

from unittest.mock import patch

import article_factory.db as db_module
from article_factory.models import PromptImprovementJob, PromptImprovementReport
from article_factory.services.telemetry_ranking import MIN_RUNS_FOR_IMPROVEMENT

from tests.test_prompt_improvement import (
    _create_queued_job,
    _make_empty_prompt_flow,
    _make_step_flow,
    _seed_completed_runs,
)


def test_post_prompt_improvement_success(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        _seed_completed_runs(db, rel_path, version_id, MIN_RUNS_FOR_IMPROVEMENT)
    finally:
        db.close()

    with patch("article_factory.routes.prompt_improvement.prompt_improvement_runner.enqueue") as enqueue:
        response = client.post(
            "/api/flows/prompt-improvement",
            headers=api_headers,
            json={
                "path": rel_path,
                "flow_version_id": version_id,
                "scope": "step",
                "target_step_key": "step_1",
                "selected_model": "llama3",
                "selected_puller": "gpu-01",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["job"]["flow_path"] == rel_path
    assert body["job"]["status"] == "queued"
    enqueue.assert_called_once_with(body["job"]["id"])


def test_post_prompt_improvement_validation_error(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    response = client.post(
        "/api/flows/prompt-improvement",
        headers=api_headers,
        json={
            "path": rel_path,
            "flow_version_id": version_id,
            "scope": "step",
            "target_step_key": "",
            "selected_model": "llama3",
            "selected_puller": "gpu-01",
        },
    )
    assert response.status_code == 400
    assert "target_step_key is required" in response.json()["detail"]


def test_post_prompt_improvement_insufficient_runs(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    response = client.post(
        "/api/flows/prompt-improvement",
        headers=api_headers,
        json={
            "path": rel_path,
            "flow_version_id": version_id,
            "scope": "flow",
            "target_step_key": "",
            "selected_model": "llama3",
            "selected_puller": "gpu-01",
        },
    )
    assert response.status_code == 400
    assert "At least 10 completed runs" in response.json()["detail"]


def test_get_prompt_improvement_steps(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    response = client.get(
        "/api/flows/prompt-improvement/steps",
        headers=api_headers,
        params={"path": rel_path, "flow_version_id": version_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["flow_path"] == rel_path
    assert body["min_completed_runs"] == MIN_RUNS_FOR_IMPROVEMENT
    assert any(step["step_key"] == "step_1" for step in body["steps"])


def test_get_prompt_improvement_steps_not_found(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    response = client.get(
        "/api/flows/prompt-improvement/steps",
        headers=api_headers,
        params={"path": "missing.flow.json", "flow_version_id": version_id},
    )
    assert response.status_code == 404


def test_get_prompt_improvement_steps_skips_empty_prompt_steps(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_empty_prompt_flow(configured_db)
    response = client.get(
        "/api/flows/prompt-improvement/steps",
        headers=api_headers,
        params={"path": rel_path, "flow_version_id": version_id},
    )
    assert response.status_code == 200
    assert response.json()["steps"] == []


def test_get_prompt_improvement_jobs_and_job(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        job = _create_queued_job(db, rel_path, version_id)
        job_id = job.id
    finally:
        db.close()

    listed = client.get(
        "/api/flows/prompt-improvement",
        headers=api_headers,
        params={"path": rel_path, "flow_version_id": version_id},
    )
    assert listed.status_code == 200
    jobs = listed.json()["jobs"]
    assert any(item["id"] == job_id for item in jobs)

    fetched = client.get(f"/api/flows/prompt-improvement/{job_id}", headers=api_headers)
    assert fetched.status_code == 200
    assert fetched.json()["job"]["id"] == job_id

    missing = client.get("/api/flows/prompt-improvement/99999", headers=api_headers)
    assert missing.status_code == 404


def test_get_prompt_improvement_report(client, api_headers, configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        job = _create_queued_job(db, rel_path, version_id)
        report = PromptImprovementReport(
            job_id=job.id,
            flow_path=rel_path,
            source_flow_version_id=version_id,
            scope="step",
            target_step_key="step_1",
            summary="Done",
            actionable_items=[],
            detailed_report="Report body",
            example_runs={"success": [], "failure": []},
            prompt_changes=[],
            selected_model="llama3",
            selected_puller="gpu-01",
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        report_id = report.id
    finally:
        db.close()

    response = client.get(f"/api/flows/prompt-improvement/reports/{report_id}", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["report"]["summary"] == "Done"

    missing = client.get("/api/flows/prompt-improvement/reports/99999", headers=api_headers)
    assert missing.status_code == 404
