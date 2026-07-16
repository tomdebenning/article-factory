from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, PromptAnalysis
from article_factory.services.flow_performance import aggregate_performance
from article_factory.services.flow_storage import read_flow
from article_factory.services.flow_versions import get_flow_version, load_version_flow
from article_factory.services.verdict import Verdict, extract_feedback_body, parse_verdict


def _collect_reject_samples(runs: list[FactoryRun], gate_key: str, *, limit: int = 5) -> list[str]:
    samples: list[str] = []
    for run in runs:
        manifest = run.manifest or {}
        for step in manifest.get("steps") or manifest.get("step_stats") or []:
            if str(step.get("step_key") or "") != gate_key:
                continue
            if parse_verdict(str(step.get("content") or "")) != Verdict.REJECT:
                continue
            feedback = extract_feedback_body(str(step.get("content") or "")).strip()
            if feedback and feedback not in samples:
                samples.append(feedback[:500])
            if len(samples) >= limit:
                return samples
    return samples


def analyze_flow_performance(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int | None = None,
    topic_queue_snapshot_id: int | None = None,
    selected_model: str | None = None,
) -> PromptAnalysis:
    metrics = aggregate_performance(
        db,
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        topic_queue_snapshot_id=topic_queue_snapshot_id,
        selected_model=selected_model,
    )
    runs = (
        db.query(FactoryRun)
        .filter(FactoryRun.flow_path == flow_path, FactoryRun.status == "completed")
        .order_by(FactoryRun.started_at.desc())
        .limit(200)
        .all()
    )
    if flow_version_id is not None:
        runs = [run for run in runs if run.flow_version_id == flow_version_id]
    if topic_queue_snapshot_id is not None:
        runs = [run for run in runs if run.topic_queue_snapshot_id == topic_queue_snapshot_id]
    if selected_model:
        runs = [run for run in runs if run.selected_model == selected_model]

    flow = read_flow(flow_path)
    if flow_version_id:
        version = get_flow_version(db, flow_version_id)
        if version:
            flow = load_version_flow(version)

    from article_factory.services.flow_performance import resolve_gate_config

    gate_key, producer_keys = resolve_gate_config(flow)
    overall = metrics["overall"]
    total_runs = int(overall.get("run_count") or 0)
    completed_count = int(overall.get("completed_count") or 0)
    completion_rate = overall.get("completion_rate")
    yield_rate = overall.get("first_pass_yield_rate")
    completed_rate = overall.get("first_pass_completed_rate") or overall.get("first_pass_rate")
    first_pass = int(overall.get("first_pass_count") or 0)

    suggestions: list[dict[str, Any]] = []
    summary_parts = [f"Analyzed {total_runs} run(s) on {flow_path}."]
    if total_runs > 0:
        pct_complete = f"{completion_rate * 100:.0f}%" if completion_rate is not None else "—"
        summary_parts.append(
            f"Artifact yield: {completed_count}/{total_runs} runs completed ({pct_complete})."
        )
    if total_runs > 0 and yield_rate is not None:
        summary_parts.append(
            f"First-pass yield (all runs): {yield_rate * 100:.0f}% "
            f"({first_pass}/{total_runs} accepted on the first review)."
        )
    if completed_count > 0 and completed_rate is not None:
        summary_parts.append(
            f"First-pass among completions: {completed_rate * 100:.0f}% "
            f"({first_pass}/{completed_count} completed without a rewrite loop)."
        )
    elif completed_count == 0:
        summary_parts.append("No completed runs in this cohort yet.")

    if gate_key and completed_count > 0:
        reject_samples = _collect_reject_samples(runs, gate_key)
        if completed_rate is not None and completed_rate < 0.7 and producer_keys:
            producer = producer_keys[0]
            step = next((item for item in flow.steps if item.step_key == producer), None)
            suggestions.append(
                {
                    "step_key": producer,
                    "diagnosis": (
                        f"First-pass accept is below 70%. The {gate_key} step rejected "
                        f"{len(reject_samples)} recent run(s) before accept."
                    ),
                    "suggestion": (
                        f"Tighten the {producer} system prompt with explicit acceptance criteria "
                        f"that mirror what {gate_key} checks. Add a pre-submit checklist the model "
                        f"must satisfy before finishing."
                    ),
                    "evidence": reject_samples[:3],
                }
            )
        if reject_samples:
            suggestions.append(
                {
                    "step_key": gate_key,
                    "diagnosis": "Recent reject feedback themes:",
                    "suggestion": (
                        f"Review the {gate_key} prompt so feedback is specific and actionable. "
                        "Ensure VERDICT: ACCEPT/REJECT is always on the final line."
                    ),
                    "evidence": reject_samples[:3],
                }
            )

    if not suggestions and total_runs >= 3 and completion_rate is not None and completion_rate >= 0.7:
        suggestions.append(
            {
                "step_key": "",
                "diagnosis": "Performance looks healthy for this cohort.",
                "suggestion": "Save a new flow version before making experimental prompt changes so you can compare.",
                "evidence": [],
            }
        )

    row = PromptAnalysis(
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        topic_queue_snapshot_id=topic_queue_snapshot_id,
        selected_model=selected_model or "",
        run_count=total_runs,
        first_pass_rate=completed_rate,
        summary=" ".join(summary_parts),
        suggestions=suggestions,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def analysis_to_dict(row: PromptAnalysis) -> dict[str, Any]:
    return {
        "id": row.id,
        "flow_path": row.flow_path,
        "flow_version_id": row.flow_version_id,
        "topic_queue_snapshot_id": row.topic_queue_snapshot_id,
        "selected_model": row.selected_model,
        "run_count": row.run_count,
        "first_pass_rate": row.first_pass_rate,
        "summary": row.summary,
        "suggestions": row.suggestions or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
