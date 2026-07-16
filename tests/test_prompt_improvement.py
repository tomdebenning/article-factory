from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

import article_factory.db as db_module
from article_factory.models import (
    FactoryRun,
    IterationTelemetry,
    PromptImprovementJob,
    PromptImprovementReport,
    RunTelemetry,
)
from article_factory.services.flow_schema import FlowStepCompletion, flow_from_dict
from article_factory.services.flow_storage import create_flow, write_flow
from article_factory.services.flow_versions import create_flow_version
from article_factory.services.telemetry import capture_run_telemetry
from article_factory.services.telemetry_ranking import (
    MIN_RUNS_FOR_IMPROVEMENT,
    RankedRun,
    composite_quality_score,
    rank_runs_for_version,
    select_example_runs,
)


def _manifest_with_content() -> dict:
    return {
        "steps": [
            {
                "step_key": "step_1",
                "content": "Draft paragraph one.",
                "duration_ms": 1000,
                "usage": {"total_tokens": 100},
                "turns": 1,
            },
            {
                "step_key": "step_2",
                "content": "ARTICLE REVIEW\n\nTOTAL SCORE: 85/100\n\nVERDICT: ACCEPTED\nEND REVIEW",
                "duration_ms": 2000,
                "usage": {"total_tokens": 200},
                "turns": 2,
            },
        ]
    }


def _seed_completed_runs(db, rel_path: str, version_id: int, count: int) -> None:
    for index in range(count):
        db.add(
            FactoryRun(
                run_id=f"rank-run-{index}",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                first_pass_accept=index % 2 == 0,
                manifest=_manifest_with_content(),
            )
        )
    db.commit()
    for index in range(count):
        capture_run_telemetry(db, f"rank-run-{index}")


def _make_step_flow(configured_db: str, *, slug: str = "rank-test") -> tuple[str, int]:
    db = db_module.SessionLocal()
    try:
        rel_path, flow = create_flow(folder="", slug=slug, display_name="Rank", step_count=2)
        flow.steps[0].step_key = "step_1"
        flow.steps[1].step_key = "step_2"
        flow.steps[1].completion = FlowStepCompletion(
            can_complete=True,
            can_loop=True,
            loop_goto_step_id=flow.steps[0].step_id,
        )
        write_flow(rel_path, flow)
        version = create_flow_version(db, rel_path, message="v1")
        db.commit()
        return rel_path, version.id
    finally:
        db.close()


def _make_empty_prompt_flow(configured_db: str) -> tuple[str, int]:
    db = db_module.SessionLocal()
    try:
        rel_path, flow = create_flow(folder="", slug="empty-prompts", display_name="Empty", step_count=1)
        flow.steps[0].step_key = "empty_step"
        flow.steps[0].system_prompt = ""
        flow.steps[0].user_prompt_template = ""
        write_flow(rel_path, flow)
        version = create_flow_version(db, rel_path, message="v1", flow=flow)
        db.commit()
        return rel_path, version.id
    finally:
        db.close()


def _llm_success_payload(*, step_key: str = "step_1") -> str:
    return json.dumps(
        {
            "summary": "Improved writer clarity",
            "actionable_items": [{"title": "Tighten openings", "priority": "high", "rationale": "Weak runs", "evidence_run_ids": ["rank-run-0"]}],
            "detailed_report": "## Telemetry overview\nObserved patterns.",
            "prompt_updates": [
                {
                    "step_key": step_key,
                    "system_prompt": "Write with explicit structure and strong openings.",
                    "user_prompt_template": "{{topic}}",
                    "rationale": "Weak runs showed vague openings.",
                    "conclusion": "Add explicit structure requirements.",
                    "evidence_run_ids": ["rank-run-0"],
                }
            ],
        }
    )


def test_capture_iteration_content(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="content-run-1",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                manifest=_manifest_with_content(),
            )
        )
        db.commit()
        capture_run_telemetry(db, "content-run-1")
        iteration = db.query(IterationTelemetry).filter_by(run_id="content-run-1").first()
        run_row = db.query(RunTelemetry).filter_by(run_id="content-run-1").first()
        assert iteration is not None
        assert "Draft paragraph" in (iteration.writer_content or "")
        assert "TOTAL SCORE" in (iteration.reviewer_content or "")
        assert run_row is not None
        assert run_row.final_article_text
    finally:
        db.close()


def test_rank_runs_top_bottom_quartile(configured_db) -> None:
    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        _seed_completed_runs(db, rel_path, version_id, 12)
        ranked = rank_runs_for_version(db, flow_path=rel_path, flow_version_id=version_id)
        successes, failures = select_example_runs(ranked)
        assert len(ranked) == 12
        assert len(successes) == 3
        assert len(failures) == 3
        assert successes[0].composite_score >= failures[-1].composite_score
    finally:
        db.close()


def test_composite_quality_score_prefers_good_runs() -> None:
    good = RunTelemetry(
        run_id="good",
        flow_path="x.flow.json",
        run_status="completed",
        final_score=95,
        iteration_count=1,
        first_pass_accept=True,
        regression_count=0,
    )
    bad = RunTelemetry(
        run_id="bad",
        flow_path="x.flow.json",
        run_status="completed",
        final_score=55,
        iteration_count=8,
        first_pass_accept=False,
        regression_count=3,
    )
    assert composite_quality_score(good, iteration_rows=[]) > composite_quality_score(bad, iteration_rows=[])
    assert MIN_RUNS_FOR_IMPROVEMENT == 10


def test_apply_prompt_updates_records_analysis_fields() -> None:
    from article_factory.services.prompt_improvement import _apply_prompt_updates

    flow = flow_from_dict(
        {
            "slug": "test-flow",
            "display_name": "Test",
            "steps": [
                {
                    "step_id": "s1",
                    "step_key": "writer",
                    "label": "Writer",
                    "order": 1,
                    "system_prompt": "Old system",
                    "user_prompt_template": "Old user {topic}",
                }
            ],
        }
    )
    changes = _apply_prompt_updates(
        flow,
        [
            {
                "step_key": "writer",
                "system_prompt": "New system with clearer structure",
                "user_prompt_template": "Old user {topic}",
                "rationale": "Weak runs showed vague openings.",
                "conclusion": "Add explicit structure requirements to reduce revision loops.",
                "evidence_run_ids": ["run-3", "run-7"],
            }
        ],
    )
    assert len(changes) == 1
    assert changes[0]["fields"] == ["system_prompt"]
    assert "vague openings" in changes[0]["rationale"]
    assert "revision loops" in changes[0]["conclusion"]
    assert changes[0]["evidence_run_ids"] == ["run-3", "run-7"]


def test_apply_prompt_updates_skips_unknown_step_and_unchanged_fields() -> None:
    from article_factory.services.prompt_improvement import _apply_prompt_updates

    flow = flow_from_dict(
        {
            "slug": "test-flow",
            "display_name": "Test",
            "steps": [
                {
                    "step_id": "s1",
                    "step_key": "writer",
                    "label": "Writer",
                    "order": 1,
                    "system_prompt": "Same",
                    "user_prompt_template": "Same user",
                }
            ],
        }
    )
    changes = _apply_prompt_updates(
        flow,
        [
            {"step_key": "missing", "system_prompt": "New"},
            {"step_key": "writer", "system_prompt": "Same", "user_prompt_template": "Same user"},
        ],
    )
    assert changes == []


def test_apply_prompt_updates_user_prompt_and_invalid_evidence() -> None:
    from article_factory.services.prompt_improvement import _apply_prompt_updates

    flow = flow_from_dict(
        {
            "slug": "test-flow",
            "display_name": "Test",
            "steps": [
                {
                    "step_id": "s1",
                    "step_key": "writer",
                    "label": "Writer",
                    "order": 1,
                    "system_prompt": "Same",
                    "user_prompt_template": "Old user",
                }
            ],
        }
    )
    changes = _apply_prompt_updates(
        flow,
        [
            {
                "step_key": "writer",
                "user_prompt_template": "New user {topic}",
                "evidence_run_ids": "not-a-list",
            }
        ],
    )
    assert changes[0]["fields"] == ["user_prompt_template"]
    assert changes[0]["evidence_run_ids"] == []


def test_list_improvement_jobs_filters_by_version(configured_db) -> None:
    from article_factory.services.flow_storage import read_flow, write_flow
    from article_factory.services.prompt_improvement import list_improvement_jobs

    rel_path, version_id = _make_step_flow(configured_db, slug="list-jobs")
    db = db_module.SessionLocal()
    try:
        _seed_completed_runs(db, rel_path, version_id, MIN_RUNS_FOR_IMPROVEMENT)
        job = _create_queued_job(db, rel_path, version_id)

        flow = read_flow(rel_path)
        flow.steps[0].system_prompt = "Modified for version two"
        write_flow(rel_path, flow)
        other_version = create_flow_version(db, rel_path, message="v2", flow=flow)
        other_job = _create_queued_job(db, rel_path, other_version.id)

        filtered = list_improvement_jobs(db, flow_path=rel_path, flow_version_id=version_id)
        assert [row.id for row in filtered] == [job.id]
        assert other_job.id not in {row.id for row in filtered}

        all_jobs = list_improvement_jobs(db, flow_path=rel_path)
        assert {row.id for row in all_jobs} == {job.id, other_job.id}
    finally:
        db.close()


def test_truncate() -> None:
    from article_factory.services.prompt_improvement import _truncate

    assert _truncate(None, 10) == ""
    assert _truncate("  short  ", 10) == "short"
    long_text = "x" * 20
    truncated = _truncate(long_text, 10)
    assert truncated.endswith("...")
    assert len(truncated) == 10


def test_improvable_steps_and_target_steps() -> None:
    from article_factory.services.prompt_improvement import _improvable_steps, _target_steps

    flow = flow_from_dict(
        {
            "slug": "test-flow",
            "display_name": "Test",
            "steps": [
                {
                    "step_id": "s1",
                    "step_key": "writer",
                    "label": "Writer",
                    "order": 1,
                    "system_prompt": "Write",
                    "user_prompt_template": "",
                },
                {
                    "step_id": "s2",
                    "step_key": "reviewer",
                    "label": "Reviewer",
                    "order": 2,
                    "system_prompt": "",
                    "user_prompt_template": "",
                },
            ],
        }
    )
    improvable = _improvable_steps(flow)
    assert len(improvable) == 1
    assert improvable[0]["step_key"] == "writer"

    all_steps = _target_steps(flow, scope="flow", target_step_key="")
    assert len(all_steps) == 1

    scoped = _target_steps(flow, scope="step", target_step_key="writer")
    assert len(scoped) == 1
    assert scoped[0]["step_key"] == "writer"

    missing = _target_steps(flow, scope="step", target_step_key="missing")
    assert missing == []


def test_aggregate_stats(configured_db) -> None:
    from article_factory.services.prompt_improvement import _aggregate_stats

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        assert _aggregate_stats(db, flow_path=rel_path, flow_version_id=version_id) == {"completed_runs": 0}

        _seed_completed_runs(db, rel_path, version_id, 4)
        stats = _aggregate_stats(db, flow_path=rel_path, flow_version_id=version_id)
        assert stats["completed_runs"] == 4
        assert stats["avg_final_score"] is not None
        assert stats["median_iterations"] is not None
        assert 0 <= stats["first_pass_rate"] <= 1
    finally:
        db.close()


def test_build_llm_messages() -> None:
    from article_factory.services.prompt_improvement import _build_llm_messages

    messages = _build_llm_messages(
        flow_path="experiments/test.flow.json",
        source_version_number=1,
        scope="step",
        target_step_key="writer",
        target_steps=[{"step_key": "writer", "label": "Writer", "system_prompt": "Old", "user_prompt_template": "{{topic}}"}],
        aggregate_stats={"completed_runs": 12},
        success_examples=[{"run_id": "good-1"}],
        failure_examples=[{"run_id": "bad-1"}],
    )
    assert messages[0]["role"] == "system"
    assert "prompt engineering coach" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    user_payload = json.loads(messages[1]["content"])
    assert user_payload["flow_path"] == "experiments/test.flow.json"
    assert user_payload["source_version"] == "v1"
    assert "step 'writer'" in user_payload["improvement_scope"]
    assert user_payload["success_examples"] == [{"run_id": "good-1"}]

    flow_scope = _build_llm_messages(
        flow_path="x.flow.json",
        source_version_number=2,
        scope="flow",
        target_step_key="",
        target_steps=[],
        aggregate_stats={},
        success_examples=[],
        failure_examples=[],
    )
    flow_payload = json.loads(flow_scope[1]["content"])
    assert flow_payload["improvement_scope"] == "entire flow"


def test_run_example_payload_truncates_long_content(configured_db) -> None:
    from article_factory.services.prompt_improvement import MAX_ARTICLE_CHARS, MAX_EXCERPT_CHARS, _run_example_payload

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="long-run",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version_id,
                status="completed",
                manifest=_manifest_with_content(),
            )
        )
        db.commit()
        capture_run_telemetry(db, "long-run")

        run_row = db.query(RunTelemetry).filter_by(run_id="long-run").first()
        assert run_row is not None
        run_row.final_article_text = "A" * (MAX_ARTICLE_CHARS + 50)
        iteration = db.query(IterationTelemetry).filter_by(run_id="long-run").first()
        assert iteration is not None
        iteration.writer_content = "W" * (MAX_EXCERPT_CHARS + 50)
        iteration.reviewer_content = "R" * (MAX_EXCERPT_CHARS + 50)
        db.commit()

        ranked = RankedRun(run_id="long-run", composite_score=0.9, bucket="top", metrics={"final_score": 90})
        payload = _run_example_payload(db, ranked=ranked)
        assert payload["run_id"] == "long-run"
        assert len(payload["final_article_text"]) <= MAX_ARTICLE_CHARS
        assert payload["iterations"][0]["writer_content"].endswith("...")
    finally:
        db.close()


def test_validate_improvement_request_errors(configured_db) -> None:
    from article_factory.services.prompt_improvement import validate_improvement_request

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        with pytest.raises(ValueError, match="Flow version not found"):
            validate_improvement_request(
                db,
                flow_path="missing.flow.json",
                flow_version_id=version_id,
                selected_model="llama3",
                selected_puller="gpu-01",
                scope="flow",
            )

        with pytest.raises(ValueError, match="scope must be"):
            validate_improvement_request(
                db,
                flow_path=rel_path,
                flow_version_id=version_id,
                selected_model="llama3",
                selected_puller="gpu-01",
                scope="invalid",
            )

        with pytest.raises(ValueError, match="target_step_key is required"):
            validate_improvement_request(
                db,
                flow_path=rel_path,
                flow_version_id=version_id,
                selected_model="llama3",
                selected_puller="gpu-01",
                scope="step",
                target_step_key="",
            )

        with pytest.raises(ValueError, match="Unknown step key"):
            validate_improvement_request(
                db,
                flow_path=rel_path,
                flow_version_id=version_id,
                selected_model="llama3",
                selected_puller="gpu-01",
                scope="step",
                target_step_key="missing-step",
            )

        with pytest.raises(ValueError, match="At least 10 completed runs"):
            validate_improvement_request(
                db,
                flow_path=rel_path,
                flow_version_id=version_id,
                selected_model="llama3",
                selected_puller="gpu-01",
                scope="flow",
            )

        _seed_completed_runs(db, rel_path, version_id, MIN_RUNS_FOR_IMPROVEMENT)

        with pytest.raises(ValueError, match="selected_model is required"):
            validate_improvement_request(
                db,
                flow_path=rel_path,
                flow_version_id=version_id,
                selected_model="",
                selected_puller="gpu-01",
                scope="flow",
            )

        with pytest.raises(ValueError, match="selected_puller is required"):
            validate_improvement_request(
                db,
                flow_path=rel_path,
                flow_version_id=version_id,
                selected_model="llama3",
                selected_puller="",
                scope="flow",
            )
    finally:
        db.close()


def test_create_improvement_job(configured_db) -> None:
    from article_factory.services.prompt_improvement import create_improvement_job

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        _seed_completed_runs(db, rel_path, version_id, MIN_RUNS_FOR_IMPROVEMENT)
        job = create_improvement_job(
            db,
            flow_path=rel_path,
            source_flow_version_id=version_id,
            scope="step",
            target_step_key="step_1",
            selected_model=" llama3 ",
            selected_puller=" gpu-01 ",
        )
        assert job.id is not None
        assert job.status == "queued"
        assert job.selected_model == "llama3"
        assert job.selected_puller == "gpu-01"
        assert job.run_count == MIN_RUNS_FOR_IMPROVEMENT
        assert job.target_step_key == "step_1"
    finally:
        db.close()


def test_job_to_dict_and_report_to_dict() -> None:
    from article_factory.services.prompt_improvement import job_to_dict, report_to_dict

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = PromptImprovementJob(
        id=7,
        flow_path="x.flow.json",
        source_flow_version_id=3,
        scope="flow",
        target_step_key="",
        status="completed",
        progress_stage="done",
        progress_percent=100,
        selected_model="llama3",
        selected_puller="gpu-01",
        run_count=12,
        result_flow_version_id=4,
        report_id=9,
        error_message=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )
    job_dict = job_to_dict(job)
    assert job_dict["id"] == 7
    assert job_dict["created_at"] == now.isoformat()
    assert job_dict["report_id"] == 9

    report = PromptImprovementReport(
        id=9,
        job_id=7,
        flow_path="x.flow.json",
        source_flow_version_id=3,
        result_flow_version_id=4,
        scope="flow",
        target_step_key="",
        summary="Better prompts",
        actionable_items=[{"title": "Fix openings"}],
        detailed_report="## Root causes",
        example_runs={"success": [], "failure": []},
        prompt_changes=[{"step_key": "writer"}],
        selected_model="llama3",
        selected_puller="gpu-01",
        created_at=now,
    )
    report_dict = report_to_dict(report)
    assert report_dict["summary"] == "Better prompts"
    assert report_dict["actionable_items"] == [{"title": "Fix openings"}]
    assert report_dict["created_at"] == now.isoformat()


@pytest.mark.asyncio
async def test_validate_puller_model(configured_db) -> None:
    from article_factory.control_plane.client import ControlPlaneClient
    from article_factory.services.prompt_improvement import validate_puller_model

    cp = AsyncMock(spec=ControlPlaneClient)
    with patch(
        "article_factory.services.prompt_improvement.get_registered_puller_on_cp",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="not registered"):
            await validate_puller_model(cp, puller="gpu-01", model="llama3")

    with patch(
        "article_factory.services.prompt_improvement.get_registered_puller_on_cp",
        new=AsyncMock(return_value={"puller_name": "gpu-01", "supported_models": ["mistral"]}),
    ):
        with pytest.raises(ValueError, match="does not support model"):
            await validate_puller_model(cp, puller="gpu-01", model="llama3")

    with patch(
        "article_factory.services.prompt_improvement.get_registered_puller_on_cp",
        new=AsyncMock(return_value={"puller_name": "gpu-01", "supported_models": ["llama3"]}),
    ):
        await validate_puller_model(cp, puller="gpu-01", model="llama3")


def _create_queued_job(db, rel_path: str, version_id: int, **overrides) -> PromptImprovementJob:
    job = PromptImprovementJob(
        flow_path=rel_path,
        source_flow_version_id=version_id,
        scope=overrides.get("scope", "step"),
        target_step_key=overrides.get("target_step_key", "step_1"),
        status=overrides.get("status", "queued"),
        progress_stage="queued",
        progress_percent=0,
        selected_model=overrides.get("selected_model", "llama3"),
        selected_puller=overrides.get("selected_puller", "gpu-01"),
        run_count=MIN_RUNS_FOR_IMPROVEMENT,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_run_prompt_improvement_job_completes(configured_db) -> None:
    from article_factory.services.prompt_improvement import run_prompt_improvement_job

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        _seed_completed_runs(db, rel_path, version_id, MIN_RUNS_FOR_IMPROVEMENT)
        job = _create_queued_job(db, rel_path, version_id)

        with (
            patch(
                "article_factory.services.prompt_improvement.validate_puller_model",
                new=AsyncMock(),
            ),
            patch(
                "article_factory.services.prompt_improvement.run_control_plane_completion",
                new=AsyncMock(return_value=_llm_success_payload()),
            ),
        ):
            await run_prompt_improvement_job(db, job.id, control_plane_url="http://control-plane")

        db.refresh(job)
        assert job.status == "completed"
        assert job.progress_percent == 100
        assert job.result_flow_version_id is not None
        assert job.report_id is not None

        report = db.get(PromptImprovementReport, job.report_id)
        assert report is not None
        assert report.summary == "Improved writer clarity"
        assert report.prompt_changes
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_prompt_improvement_job_early_returns_and_failures(configured_db) -> None:
    from article_factory.services.prompt_improvement import run_prompt_improvement_job

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        await run_prompt_improvement_job(db, 99999, control_plane_url="http://control-plane")

        completed_job = _create_queued_job(db, rel_path, version_id, status="completed")
        await run_prompt_improvement_job(db, completed_job.id, control_plane_url="http://control-plane")
        db.refresh(completed_job)
        assert completed_job.status == "completed"

        missing_version_job = PromptImprovementJob(
            flow_path=rel_path,
            source_flow_version_id=99999,
            scope="flow",
            status="queued",
            selected_model="llama3",
            selected_puller="gpu-01",
        )
        db.add(missing_version_job)
        db.commit()
        db.refresh(missing_version_job)
        await run_prompt_improvement_job(db, missing_version_job.id, control_plane_url="http://control-plane")
        db.refresh(missing_version_job)
        assert missing_version_job.status == "failed"
        assert "Source flow version not found" in (missing_version_job.error_message or "")

        rel_path_no_prompts, version_id_no_prompts = _make_empty_prompt_flow(configured_db)
        no_prompt_job = _create_queued_job(
            db,
            rel_path_no_prompts,
            version_id_no_prompts,
            scope="step",
            target_step_key="empty_step",
        )
        await run_prompt_improvement_job(db, no_prompt_job.id, control_plane_url="http://control-plane")
        db.refresh(no_prompt_job)
        assert no_prompt_job.status == "failed"
        assert "No editable prompts" in (no_prompt_job.error_message or "")

        _seed_completed_runs(db, rel_path, version_id, MIN_RUNS_FOR_IMPROVEMENT)
        llm_fail_job = _create_queued_job(db, rel_path, version_id)
        with patch(
            "article_factory.services.prompt_improvement.validate_puller_model",
            new=AsyncMock(side_effect=ValueError("bad puller")),
        ):
            await run_prompt_improvement_job(db, llm_fail_job.id, control_plane_url="http://control-plane")
        db.refresh(llm_fail_job)
        assert llm_fail_job.status == "failed"
        assert "bad puller" in (llm_fail_job.error_message or "")

        no_updates_job = _create_queued_job(db, rel_path, version_id)
        with (
            patch("article_factory.services.prompt_improvement.validate_puller_model", new=AsyncMock()),
            patch(
                "article_factory.services.prompt_improvement.run_control_plane_completion",
                new=AsyncMock(return_value=json.dumps({"summary": "noop", "prompt_updates": []})),
            ),
        ):
            await run_prompt_improvement_job(db, no_updates_job.id, control_plane_url="http://control-plane")
        db.refresh(no_updates_job)
        assert no_updates_job.status == "failed"
        assert "did not return any applicable prompt updates" in (no_updates_job.error_message or "")

        version_fail_job = _create_queued_job(db, rel_path, version_id)
        with (
            patch("article_factory.services.prompt_improvement.validate_puller_model", new=AsyncMock()),
            patch(
                "article_factory.services.prompt_improvement.run_control_plane_completion",
                new=AsyncMock(return_value=_llm_success_payload()),
            ),
            patch(
                "article_factory.services.prompt_improvement.create_improved_flow_version",
                side_effect=RuntimeError("version write failed"),
            ),
        ):
            await run_prompt_improvement_job(db, version_fail_job.id, control_plane_url="http://control-plane")
        db.refresh(version_fail_job)
        assert version_fail_job.status == "failed"
        assert "version write failed" in (version_fail_job.error_message or "")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_prompt_improvement_runner_enqueue_dedupes(configured_db, monkeypatch) -> None:
    from article_factory.services.prompt_improvement_runner import PromptImprovementRunner

    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.SessionLocal",
        db_module.SessionLocal,
    )

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        queued_job = _create_queued_job(db, rel_path, version_id)
        job_id = queued_job.id
    finally:
        db.close()

    runner = PromptImprovementRunner()
    mock_run = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.run_prompt_improvement_job",
        mock_run,
    )
    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.load_runtime_settings",
        lambda _db: type("Runtime", (), {"control_plane_url": "http://cp"})(),
    )

    runner.enqueue(job_id)
    runner.enqueue(job_id)
    await _wait_for(mock_run)
    assert mock_run.await_count == 1


async def _wait_for(mock: AsyncMock, timeout: float = 1.0) -> None:
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while mock.await_count == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    if mock.await_count == 0:
        raise AssertionError("mock was not awaited")


@pytest.mark.asyncio
async def test_prompt_improvement_runner_start_marks_interrupted_running_failed(configured_db, monkeypatch) -> None:
    from article_factory.services.prompt_improvement_runner import PromptImprovementRunner

    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.SessionLocal",
        db_module.SessionLocal,
    )

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        running_job = _create_queued_job(db, rel_path, version_id, status="running")
        running_job.progress_stage = "calling_llm"
        db.commit()
        running_job_id = running_job.id
    finally:
        db.close()

    runner = PromptImprovementRunner()
    await runner.start()

    check_db = db_module.SessionLocal()
    try:
        updated_running = check_db.get(PromptImprovementJob, running_job_id)
        assert updated_running is not None
        assert updated_running.status == "failed"
        assert "Interrupted by factory restart" in (updated_running.error_message or "")
    finally:
        check_db.close()


@pytest.mark.asyncio
async def test_prompt_improvement_runner_marks_crashed_job_failed(configured_db, monkeypatch) -> None:
    from article_factory.services.prompt_improvement_runner import PromptImprovementRunner

    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.SessionLocal",
        db_module.SessionLocal,
    )

    rel_path, version_id = _make_step_flow(configured_db)
    db = db_module.SessionLocal()
    try:
        job = _create_queued_job(db, rel_path, version_id)
        job_id = job.id
    finally:
        db.close()

    runner = PromptImprovementRunner()

    async def boom(_db, job_id, *, control_plane_url):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.run_prompt_improvement_job",
        boom,
    )
    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.load_runtime_settings",
        lambda _db: type("Runtime", (), {"control_plane_url": "http://cp"})(),
    )

    await runner._run_job(job_id)

    check_db = db_module.SessionLocal()
    try:
        updated = check_db.get(PromptImprovementJob, job_id)
        assert updated is not None
        assert updated.status == "failed"
        assert "Unexpected error" in (updated.error_message or "")
    finally:
        check_db.close()


@pytest.mark.asyncio
async def test_prompt_improvement_runner_start_requeues_stale_queued_jobs(configured_db, monkeypatch) -> None:
    from article_factory.services.prompt_improvement_runner import PromptImprovementRunner

    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.SessionLocal",
        db_module.SessionLocal,
    )

    rel_path, version_id = _make_step_flow(configured_db, slug="stale-queued")
    db = db_module.SessionLocal()
    try:
        queued_job = _create_queued_job(db, rel_path, version_id)
        queued_job_id = queued_job.id
    finally:
        db.close()

    runner = PromptImprovementRunner()
    mock_run = AsyncMock()
    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.run_prompt_improvement_job",
        mock_run,
    )
    monkeypatch.setattr(
        "article_factory.services.prompt_improvement_runner.load_runtime_settings",
        lambda _db: type("Runtime", (), {"control_plane_url": "http://cp"})(),
    )

    await runner.start()
    await _wait_for(mock_run)
    assert mock_run.await_args.args[1] == queued_job_id
