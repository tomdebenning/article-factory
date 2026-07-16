"""LLM-driven prompt improvement from telemetry and example runs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import (
    IterationTelemetry,
    PromptImprovementJob,
    PromptImprovementReport,
    RunTelemetry,
)
from article_factory.services.control_plane_completion import extract_json_object, run_control_plane_completion
from article_factory.services.flow_schema import FlowDefinition, flow_from_dict, flow_to_dict, strip_runtime_overrides
from article_factory.services.flow_versions import (
    create_improved_flow_version,
    get_flow_version,
    load_version_flow,
    peek_next_version_number,
)
from article_factory.services.puller_selection import get_registered_puller_on_cp, puller_supports_model
from article_factory.services.telemetry_ranking import (
    MIN_RUNS_FOR_IMPROVEMENT,
    RankedRun,
    rank_runs_for_version,
    select_example_runs,
)

logger = logging.getLogger(__name__)

MAX_EXCERPT_CHARS = 6000
MAX_ARTICLE_CHARS = 12000


def _truncate(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _improvable_steps(flow: FlowDefinition) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
    for step in flow.steps:
        if (step.system_prompt or "").strip() or (step.user_prompt_template or "").strip():
            steps.append(
                {
                    "step_key": step.step_key,
                    "label": step.label,
                    "system_prompt": step.system_prompt,
                    "user_prompt_template": step.user_prompt_template,
                }
            )
    return steps


def _target_steps(flow: FlowDefinition, *, scope: str, target_step_key: str) -> list[dict[str, str]]:
    steps = _improvable_steps(flow)
    if scope == "flow":
        return steps
    key = target_step_key.strip()
    return [step for step in steps if step["step_key"] == key]


def _run_example_payload(
    db: Session,
    *,
    ranked: RankedRun,
) -> dict[str, Any]:
    row = db.query(RunTelemetry).filter_by(run_id=ranked.run_id).first()
    iterations = (
        db.query(IterationTelemetry)
        .filter_by(run_id=ranked.run_id)
        .order_by(IterationTelemetry.iteration_number.asc())
        .all()
    )
    return {
        "run_id": ranked.run_id,
        "bucket": ranked.bucket,
        "composite_score": ranked.composite_score,
        "metrics": ranked.metrics,
        "final_article_text": _truncate(row.final_article_text if row else None, MAX_ARTICLE_CHARS),
        "iterations": [
            {
                "iteration_number": item.iteration_number,
                "writer_step_key": item.writer_step_key,
                "reviewer_step_key": item.reviewer_step_key,
                "total_score": item.total_score,
                "accepted": item.accepted,
                "writer_content": _truncate(item.writer_content, MAX_EXCERPT_CHARS),
                "reviewer_content": _truncate(item.reviewer_content, MAX_EXCERPT_CHARS),
            }
            for item in iterations
        ],
    }


def _aggregate_stats(db: Session, *, flow_path: str, flow_version_id: int) -> dict[str, Any]:
    rows = (
        db.query(RunTelemetry)
        .filter_by(flow_path=flow_path, flow_version_id=flow_version_id, run_status="completed")
        .all()
    )
    if not rows:
        return {"completed_runs": 0}
    scores = [row.final_score for row in rows if row.final_score is not None]
    iterations = [row.iteration_count for row in rows if row.iteration_count is not None]
    first_pass = sum(1 for row in rows if row.first_pass_accept)
    return {
        "completed_runs": len(rows),
        "avg_final_score": round(sum(scores) / len(scores), 2) if scores else None,
        "median_iterations": sorted(iterations)[len(iterations) // 2] if iterations else None,
        "first_pass_rate": round(first_pass / len(rows), 3),
    }


def _build_llm_messages(
    *,
    flow_path: str,
    source_version_number: int,
    scope: str,
    target_step_key: str,
    target_steps: list[dict[str, str]],
    aggregate_stats: dict[str, Any],
    success_examples: list[dict[str, Any]],
    failure_examples: list[dict[str, Any]],
) -> list[dict[str, str]]:
    scope_label = "entire flow" if scope == "flow" else f"step '{target_step_key}'"
    system = (
        "You are a prompt engineering coach for an article factory. "
        "Analyze telemetry and example runs, then propose concrete prompt improvements. "
        "Respond with a single JSON object only (no markdown outside the JSON). "
        "Your job is to explain your reasoning: what you observed in telemetry, "
        "what conclusions you drew, and why each prompt change follows from that evidence. "
        "Use this schema:\n"
        "{\n"
        '  "summary": "short changelog for the new version",\n'
        '  "actionable_items": [{"title": "", "priority": "high|medium|low", "rationale": "", "evidence_run_ids": []}],\n'
        '  "detailed_report": "markdown with required sections: '
        "## Telemetry overview, ## Patterns in strong runs, ## Patterns in weak runs, "
        "## Root causes, ## Conclusions, ## Change rationale. "
        'Each section must cite specific evidence from the example runs.",\n'
        '  "prompt_updates": [{"step_key": "", "system_prompt": "", "user_prompt_template": "", '
        '"rationale": "paragraph explaining why this change addresses the conclusions", '
        '"conclusion": "one-sentence conclusion that justified this change", '
        '"evidence_run_ids": ["run_id"]}]\n'
        "}\n"
        "Only include prompt_updates for steps you are improving. "
        "Every prompt_update must include rationale, conclusion, and evidence_run_ids when possible. "
        "The detailed_report must be substantive — not a restatement of the summary. "
        "Preserve placeholders like {topic}, {feedback}, {draft}, and step keys. "
        "Make prompts specific and testable; do not remove safety or formatting requirements."
    )
    user = {
        "flow_path": flow_path,
        "source_version": f"v{source_version_number}",
        "improvement_scope": scope_label,
        "aggregate_stats": aggregate_stats,
        "current_prompts": target_steps,
        "success_examples": success_examples,
        "failure_examples": failure_examples,
    }
    import json

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, indent=2)},
    ]


def _apply_prompt_updates(flow: FlowDefinition, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    by_key = {step.step_key: step for step in flow.steps}
    for update in updates:
        key = str(update.get("step_key") or "").strip()
        step = by_key.get(key)
        if step is None:
            continue
        changed_fields: list[str] = []
        system_prompt = str(update.get("system_prompt") or "").strip()
        user_prompt = str(update.get("user_prompt_template") or "").strip()
        if system_prompt and system_prompt != step.system_prompt:
            step.system_prompt = system_prompt
            changed_fields.append("system_prompt")
        if user_prompt and user_prompt != step.user_prompt_template:
            step.user_prompt_template = user_prompt
            changed_fields.append("user_prompt_template")
        if changed_fields:
            evidence = update.get("evidence_run_ids") or []
            if not isinstance(evidence, list):
                evidence = []
            changes.append(
                {
                    "step_key": key,
                    "label": step.label,
                    "fields": changed_fields,
                    "rationale": str(update.get("rationale") or "").strip(),
                    "conclusion": str(update.get("conclusion") or "").strip(),
                    "evidence_run_ids": [str(item).strip() for item in evidence if str(item).strip()],
                }
            )
    return changes


def job_to_dict(row: PromptImprovementJob) -> dict[str, Any]:
    return {
        "id": row.id,
        "flow_path": row.flow_path,
        "source_flow_version_id": row.source_flow_version_id,
        "scope": row.scope,
        "target_step_key": row.target_step_key,
        "status": row.status,
        "progress_stage": row.progress_stage,
        "progress_percent": row.progress_percent,
        "selected_model": row.selected_model,
        "selected_puller": row.selected_puller,
        "run_count": row.run_count,
        "result_flow_version_id": row.result_flow_version_id,
        "report_id": row.report_id,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def report_to_dict(row: PromptImprovementReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "flow_path": row.flow_path,
        "source_flow_version_id": row.source_flow_version_id,
        "result_flow_version_id": row.result_flow_version_id,
        "scope": row.scope,
        "target_step_key": row.target_step_key,
        "summary": row.summary,
        "actionable_items": row.actionable_items or [],
        "detailed_report": row.detailed_report,
        "example_runs": row.example_runs or {},
        "prompt_changes": row.prompt_changes or [],
        "selected_model": row.selected_model,
        "selected_puller": row.selected_puller,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def validate_improvement_request(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int,
    selected_model: str,
    selected_puller: str,
    scope: str,
    target_step_key: str = "",
) -> None:
    version = get_flow_version(db, flow_version_id)
    if version is None or version.flow_path != flow_path:
        raise ValueError("Flow version not found for this flow path")

    if scope not in {"step", "flow"}:
        raise ValueError("scope must be 'step' or 'flow'")
    if scope == "step" and not target_step_key.strip():
        raise ValueError("target_step_key is required for step-scoped improvement")

    flow = load_version_flow(version)
    if scope == "step":
        keys = {step.step_key for step in flow.steps}
        if target_step_key.strip() not in keys:
            raise ValueError(f"Unknown step key: {target_step_key}")

    completed_count = (
        db.query(RunTelemetry)
        .filter_by(flow_path=flow_path, flow_version_id=flow_version_id, run_status="completed")
        .count()
    )
    if completed_count < MIN_RUNS_FOR_IMPROVEMENT:
        raise ValueError(
            f"At least {MIN_RUNS_FOR_IMPROVEMENT} completed runs are required "
            f"(found {completed_count})"
        )

    if not selected_model.strip():
        raise ValueError("selected_model is required")
    if not selected_puller.strip():
        raise ValueError("selected_puller is required")


async def validate_puller_model(cp: ControlPlaneClient, *, puller: str, model: str) -> None:
    registered = await get_registered_puller_on_cp(cp, puller)
    if registered is None:
        raise ValueError(f"Puller '{puller}' is not registered on the control plane")
    if not puller_supports_model(registered, model):
        raise ValueError(f"Puller '{puller}' does not support model '{model}'")


def create_improvement_job(
    db: Session,
    *,
    flow_path: str,
    source_flow_version_id: int,
    scope: str,
    target_step_key: str,
    selected_model: str,
    selected_puller: str,
) -> PromptImprovementJob:
    validate_improvement_request(
        db,
        flow_path=flow_path,
        flow_version_id=source_flow_version_id,
        selected_model=selected_model,
        selected_puller=selected_puller,
        scope=scope,
        target_step_key=target_step_key,
    )
    completed_count = (
        db.query(RunTelemetry)
        .filter_by(
            flow_path=flow_path,
            flow_version_id=source_flow_version_id,
            run_status="completed",
        )
        .count()
    )
    row = PromptImprovementJob(
        flow_path=flow_path,
        source_flow_version_id=source_flow_version_id,
        scope=scope,
        target_step_key=target_step_key.strip(),
        status="queued",
        progress_stage="queued",
        progress_percent=0,
        selected_model=selected_model.strip(),
        selected_puller=selected_puller.strip(),
        run_count=completed_count,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _update_job_progress(
    db: Session,
    job: PromptImprovementJob,
    *,
    status: str | None = None,
    progress_stage: str | None = None,
    progress_percent: int | None = None,
    error_message: str | None = None,
    result_flow_version_id: int | None = None,
    report_id: int | None = None,
) -> None:
    if status is not None:
        job.status = status
    if progress_stage is not None:
        job.progress_stage = progress_stage
    if progress_percent is not None:
        job.progress_percent = progress_percent
    if error_message is not None:
        job.error_message = error_message
    if result_flow_version_id is not None:
        job.result_flow_version_id = result_flow_version_id
    if report_id is not None:
        job.report_id = report_id
    if status in {"completed", "failed"}:
        job.completed_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


async def run_prompt_improvement_job(db: Session, job_id: int, *, control_plane_url: str) -> None:
    job = db.get(PromptImprovementJob, job_id)
    if job is None:
        return
    if job.status not in {"queued", "running"}:
        return

    _update_job_progress(db, job, status="running", progress_stage="loading_telemetry", progress_percent=5)

    version = get_flow_version(db, job.source_flow_version_id)
    if version is None:
        _update_job_progress(db, job, status="failed", error_message="Source flow version not found")
        return

    flow = load_version_flow(version)
    target_steps = _target_steps(flow, scope=job.scope, target_step_key=job.target_step_key)
    if not target_steps:
        _update_job_progress(db, job, status="failed", error_message="No editable prompts found for this scope")
        return

    ranked = rank_runs_for_version(db, flow_path=job.flow_path, flow_version_id=job.source_flow_version_id)
    successes, failures = select_example_runs(ranked)
    success_payload = [_run_example_payload(db, ranked=item) for item in successes]
    failure_payload = [_run_example_payload(db, ranked=item) for item in failures]
    aggregate_stats = _aggregate_stats(db, flow_path=job.flow_path, flow_version_id=job.source_flow_version_id)

    _update_job_progress(db, job, progress_stage="calling_llm", progress_percent=35)

    cp = ControlPlaneClient(control_plane_url)
    try:
        await validate_puller_model(cp, puller=job.selected_puller, model=job.selected_model)
        messages = _build_llm_messages(
            flow_path=job.flow_path,
            source_version_number=version.version_number,
            scope=job.scope,
            target_step_key=job.target_step_key,
            target_steps=target_steps,
            aggregate_stats=aggregate_stats,
            success_examples=success_payload,
            failure_examples=failure_payload,
        )
        raw = await run_control_plane_completion(
            cp=cp,
            puller=job.selected_puller,
            model=job.selected_model,
            messages=messages,
        )
        payload = extract_json_object(raw)
    except Exception as exc:
        logger.exception("Prompt improvement LLM failed for job %s", job_id)
        _update_job_progress(db, job, status="failed", error_message=str(exc))
        return

    _update_job_progress(db, job, progress_stage="creating_version", progress_percent=75)

    prompt_updates = list(payload.get("prompt_updates") or [])
    improved_flow = flow_from_dict(flow_to_dict(strip_runtime_overrides(flow)))
    prompt_changes = _apply_prompt_updates(improved_flow, prompt_updates)
    if not prompt_changes:
        _update_job_progress(
            db,
            job,
            status="failed",
            error_message="LLM did not return any applicable prompt updates",
        )
        return

    summary = str(payload.get("summary") or "Prompt improvement").strip()
    next_version_number = peek_next_version_number(db, job.flow_path)
    scope_suffix = f" ({job.target_step_key})" if job.scope == "step" and job.target_step_key else ""
    version_message = f"v{next_version_number}-improved-from-v{version.version_number}{scope_suffix}: {summary}"
    try:
        new_version = create_improved_flow_version(
            db,
            job.flow_path,
            flow=improved_flow,
            source_version_number=version.version_number,
            message=version_message,
        )
    except Exception as exc:
        logger.exception("Failed creating improved flow version for job %s", job_id)
        _update_job_progress(db, job, status="failed", error_message=str(exc))
        return

    report = PromptImprovementReport(
        job_id=job.id,
        flow_path=job.flow_path,
        source_flow_version_id=job.source_flow_version_id,
        result_flow_version_id=new_version.id,
        scope=job.scope,
        target_step_key=job.target_step_key,
        summary=str(payload.get("summary") or "").strip(),
        actionable_items=list(payload.get("actionable_items") or []),
        detailed_report=str(payload.get("detailed_report") or "").strip(),
        example_runs={"success": success_payload, "failure": failure_payload},
        prompt_changes=prompt_changes,
        selected_model=job.selected_model,
        selected_puller=job.selected_puller,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    _update_job_progress(
        db,
        job,
        status="completed",
        progress_stage="done",
        progress_percent=100,
        result_flow_version_id=new_version.id,
        report_id=report.id,
    )


def list_improvement_jobs(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int | None = None,
) -> list[PromptImprovementJob]:
    query = db.query(PromptImprovementJob).filter_by(flow_path=flow_path)
    if flow_version_id is not None:
        query = query.filter_by(source_flow_version_id=flow_version_id)
    return query.order_by(PromptImprovementJob.created_at.desc()).limit(50).all()


def get_improvement_report(db: Session, report_id: int) -> PromptImprovementReport | None:
    return db.get(PromptImprovementReport, report_id)
