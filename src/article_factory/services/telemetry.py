"""Telemetry aggregation, persistence, and export for factory runs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import (
    CriterionTelemetry,
    FactoryRun,
    IterationTelemetry,
    ReviewIssueTelemetry,
    RunTelemetry,
    StepExecution,
)
from article_factory.services.flow_performance import compute_first_pass_accept
from article_factory.services.flow_roles import FlowRoleConfig, group_steps_into_iterations, resolve_flow_roles
from article_factory.services.flow_schema import FlowDefinition
from article_factory.services.review_parser import (
    StructuredReview,
    issue_resolution_counts,
    parse_structured_review,
)
from article_factory.services.token_usage import aggregate_usage_stats
from article_factory.services.verdict import Verdict, parse_verdict

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _load_flow_for_run(db: Session, run: FactoryRun) -> FlowDefinition | None:
    from article_factory.services.flow_versions import get_flow_version, load_version_flow
    from article_factory.services.flow_storage import read_flow

    if run.flow_version_id:
        version = get_flow_version(db, run.flow_version_id)
        if version and version.flow_content:
            try:
                return load_version_flow(version)
            except Exception:
                logger.warning("Could not load flow version %s for %s", run.flow_version_id, run.run_id)
    flow_path = (run.flow_path or "").strip()
    if not flow_path:
        return None
    try:
        return read_flow(flow_path)
    except Exception:
        logger.warning("Could not read flow %s for %s", flow_path, run.run_id)
        return None


def _step_content(record: dict[str, Any]) -> str:
    return str(record.get("content") or record.get("response_content") or "")


def _writer_iteration_content(writer_records: list[dict[str, Any]]) -> str:
    parts = [_step_content(record).strip() for record in writer_records if _step_content(record).strip()]
    return "\n\n---\n\n".join(parts)


def _extract_final_article_text(
    *,
    run: FactoryRun,
    flow: FlowDefinition | None,
    step_records: list[dict[str, Any]],
) -> str | None:
    from article_factory.services.flow_variables import article_body

    if flow is not None:
        step_outputs: dict[str, str] = {}
        for record in step_records:
            key = str(record.get("step_key") or "")
            step_id = str(record.get("step_id") or "")
            content = _step_content(record)
            if key:
                step_outputs[key] = content
            if step_id:
                step_outputs[step_id] = content
        body = article_body(flow, flow.steps, step_outputs).strip()
        if body:
            return body

    producer_records = [
        _step_content(record)
        for record in step_records
        if _step_content(record).strip()
    ]
    if producer_records:
        return producer_records[-1].strip()
    return None


def _final_article_from_db(
    db: Session,
    run: FactoryRun,
    step_records: list[dict[str, Any]],
    flow: FlowDefinition | None,
) -> str | None:
    from article_factory.models import CompletedArticle

    article = db.query(CompletedArticle).filter_by(run_id=run.run_id).first()
    if article and article.body_markdown.strip():
        return article.body_markdown.strip()
    return _extract_final_article_text(run=run, flow=flow, step_records=step_records)


def _usage_tokens(usage: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    if not usage:
        return None, None, None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    return (
        int(input_tokens) if input_tokens is not None else None,
        int(output_tokens) if output_tokens is not None else None,
        int(total_tokens) if total_tokens is not None else None,
    )


def _records_from_run(db: Session, run: FactoryRun) -> list[dict[str, Any]]:
    manifest_steps = list((run.manifest or {}).get("step_stats") or (run.manifest or {}).get("steps") or [])
    if manifest_steps:
        return manifest_steps

    pipeline_records = list((run.pipeline_state or {}).get("step_records") or [])
    if pipeline_records:
        return pipeline_records

    executions = (
        db.query(StepExecution)
        .filter_by(run_id=run.run_id)
        .order_by(StepExecution.id.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "step_key": row.step_key,
            "content": row.response_content or "",
            "response_content": row.response_content or "",
            "duration_ms": row.duration_ms,
            "usage": row.usage or {},
            "turns": row.turns,
            "model": row.model,
            "puller": row.puller,
        }
        for row in executions
        if row.status == "completed"
    ]


def _execution_lookup(db: Session, run_id: str) -> dict[tuple[str, int], StepExecution]:
    rows = (
        db.query(StepExecution)
        .filter_by(run_id=run_id, status="completed")
        .order_by(StepExecution.id.asc())
        .all()
    )
    counts: dict[str, int] = {}
    lookup: dict[tuple[str, int], StepExecution] = {}
    for row in rows:
        counts[row.step_key] = counts.get(row.step_key, 0) + 1
        lookup[(row.step_key, counts[row.step_key])] = row
    return lookup


def _infer_termination_reason(run: FactoryRun, *, final_accepted: bool | None) -> str:
    if run.status == "cancelled":
        return "cancelled"
    error = (run.error or "").strip().lower()
    if run.status == "failed":
        if "max flow iterations" in error or "max iterations" in error:
            return "max_iterations"
        if "missing verdict" in error or "verdict:" in error:
            return "no_verdict"
        return "failed"
    if run.status == "completed":
        if final_accepted is True:
            return "accepted"
        if final_accepted is False:
            return "failed"
        return "accepted"
    return "unknown"


def _score_transitions(scores: list[int | None]) -> tuple[int, int]:
    regression = 0
    no_progress = 0
    previous: int | None = None
    for score in scores:
        if score is None:
            continue
        if previous is not None:
            if score < previous:
                regression += 1
            elif score == previous:
                no_progress += 1
        previous = score
    return regression, no_progress


def _aggregate_iteration_metrics(
    *,
    roles: FlowRoleConfig,
    step_records: list[dict[str, Any]],
    execution_lookup: dict[tuple[str, int], StepExecution],
    attempt_number: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    groups = group_steps_into_iterations(step_records, roles)
    iteration_rows: list[dict[str, Any]] = []
    criterion_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    warning_count = 0

    writer_counts: dict[str, int] = {}
    reviewer_counts: dict[str, int] = {}
    previous_score: int | None = None

    for group in groups:
        writer_records = group.writer_records
        reviewer_record = group.reviewer_record
        writer_key = writer_records[-1].get("step_key") if writer_records else ""
        reviewer_key = reviewer_record.get("step_key") if reviewer_record else ""

        if writer_key:
            writer_counts[str(writer_key)] = writer_counts.get(str(writer_key), 0) + 1
        if reviewer_key:
            reviewer_counts[str(reviewer_key)] = reviewer_counts.get(str(reviewer_key), 0) + 1

        writer_exec = (
            execution_lookup.get((str(writer_key), writer_counts[str(writer_key)]))
            if writer_key
            else None
        )
        reviewer_exec = (
            execution_lookup.get((str(reviewer_key), reviewer_counts[str(reviewer_key)]))
            if reviewer_key
            else None
        )

        writer_duration = sum(int(r.get("duration_ms") or 0) for r in writer_records) or None
        reviewer_duration = int(reviewer_record.get("duration_ms") or 0) if reviewer_record else None
        combined_duration = None
        if writer_duration is not None or reviewer_duration is not None:
            combined_duration = (writer_duration or 0) + (reviewer_duration or 0)

        usage_records = writer_records + ([reviewer_record] if reviewer_record else [])
        usage_stats = aggregate_usage_stats(usage_records)
        turns = sum(int(r.get("turns") or 0) for r in usage_records) or None

        review_content = _step_content(reviewer_record) if reviewer_record else ""
        structured = parse_structured_review(review_content) if review_content else None
        runtime_verdict = parse_verdict(review_content) if review_content else Verdict.NONE
        verdict_value = None
        accepted = None
        if structured and structured.verdict:
            verdict_value = structured.verdict
            accepted = structured.verdict == "accepted"
        elif runtime_verdict != Verdict.NONE:
            verdict_value = "accepted" if runtime_verdict == Verdict.ACCEPT else "rejected"
            accepted = runtime_verdict == Verdict.ACCEPT

        total_score = structured.total_score if structured else None
        score_delta = None
        if total_score is not None and previous_score is not None:
            score_delta = total_score - previous_score
        if total_score is not None:
            previous_score = total_score

        issue_counts = issue_resolution_counts(structured)
        parse_warning = "; ".join(structured.parse_warnings) if structured and structured.parse_warnings else None
        if parse_warning:
            warning_count += len(structured.parse_warnings)  # type: ignore[union-attr]

        iteration_rows.append(
            {
                "run_id": None,
                "iteration_number": group.iteration_number,
                "attempt_number": attempt_number,
                "writer_step_execution_id": writer_exec.id if writer_exec else None,
                "reviewer_step_execution_id": reviewer_exec.id if reviewer_exec else None,
                "writer_step_key": str(writer_key or ""),
                "reviewer_step_key": str(reviewer_key or ""),
                "writer_model": str(writer_records[-1].get("model") or "") if writer_records else "",
                "reviewer_model": str(reviewer_record.get("model") or "") if reviewer_record else "",
                "verdict": verdict_value,
                "accepted": accepted,
                "total_score": total_score,
                "score_delta": score_delta,
                "input_tokens": usage_stats.get("input_tokens"),
                "output_tokens": usage_stats.get("output_tokens"),
                "total_tokens": usage_stats.get("total_tokens"),
                "turns": turns,
                "duration_ms": combined_duration,
                "writer_duration_ms": writer_duration,
                "reviewer_duration_ms": reviewer_duration,
                "required_change_count": issue_counts["required_change_count"],
                "fixed_issue_count": issue_counts["fixed_issue_count"],
                "partially_fixed_issue_count": issue_counts["partially_fixed_issue_count"],
                "not_fixed_issue_count": issue_counts["not_fixed_issue_count"],
                "regressed_issue_count": issue_counts["regressed_issue_count"],
                "structured_review_valid": bool(structured and structured.structured_review_valid),
                "parse_warning": parse_warning,
                "writer_content": _writer_iteration_content(writer_records) or None,
                "reviewer_content": review_content or None,
            }
        )

        if structured:
            for criterion in structured.criteria:
                criterion_rows.append(
                    {
                        "run_id": None,
                        "iteration_number": group.iteration_number,
                        "attempt_number": attempt_number,
                        "criterion_key": criterion.criterion_key,
                        "criterion_label": criterion.criterion_label,
                        "score": criterion.score,
                        "max_score": criterion.max_score,
                        "comment": criterion.comment,
                    }
                )
            for issue in structured.previous_issues + structured.required_changes:
                issue_rows.append(
                    {
                        "run_id": None,
                        "iteration_number": group.iteration_number,
                        "attempt_number": attempt_number,
                        "issue_number": issue.issue_number,
                        "category": issue.category,
                        "status": issue.status,
                        "problem": issue.problem,
                        "why_it_loses_points": issue.why_it_loses_points,
                        "required_change": issue.required_change,
                    }
                )

    return iteration_rows, criterion_rows, issue_rows, warning_count


def _compute_run_level(
    *,
    run: FactoryRun,
    roles: FlowRoleConfig,
    step_records: list[dict[str, Any]],
    iteration_rows: list[dict[str, Any]],
    warning_count: int,
    flow: FlowDefinition | None,
    db: Session | None = None,
) -> dict[str, Any]:
    review_scores = [row.get("total_score") for row in iteration_rows if row.get("total_score") is not None]
    valid_scores = [int(score) for score in review_scores if score is not None]

    initial_score = valid_scores[0] if valid_scores else None
    final_score = valid_scores[-1] if valid_scores else None
    highest_score = max(valid_scores) if valid_scores else None
    lowest_score = min(valid_scores) if valid_scores else None
    score_change = None
    if initial_score is not None and final_score is not None:
        score_change = final_score - initial_score

    regression_count, no_progress_count = _score_transitions(
        [row.get("total_score") for row in iteration_rows]
    )

    usage_stats = aggregate_usage_stats(step_records)
    llm_duration = sum(int(record.get("duration_ms") or 0) for record in step_records) or None
    wall_clock = None
    if run.started_at and run.finished_at:
        wall_clock = int((run.finished_at - run.started_at).total_seconds() * 1000)

    review_iterations = [row for row in iteration_rows if row.get("reviewer_step_key")]
    final_iteration = review_iterations[-1] if review_iterations else None
    final_accepted = final_iteration.get("accepted") if final_iteration else None

    first_pass = None
    if flow is not None:
        first_pass = compute_first_pass_accept(flow, step_records)
    if run.first_pass_accept is not None and first_pass is not None and run.first_pass_accept != first_pass:
        logger.warning(
            "first_pass_accept mismatch for %s: run=%s computed=%s",
            run.run_id,
            run.first_pass_accept,
            first_pass,
        )
        warning_count += 1
    if run.first_pass_accept is not None:
        first_pass = run.first_pass_accept

    draft_count = sum(
        1
        for record in step_records
        if str(record.get("step_key") or "") in roles.producer_step_key_set
    )
    review_count = sum(
        1
        for record in step_records
        if roles.gate_step_key and str(record.get("step_key") or "") == roles.gate_step_key
    )

    final_article_text = None
    if db is not None:
        final_article_text = _final_article_from_db(db, run, step_records, flow)

    return {
        "run_id": run.run_id,
        "flow_path": run.flow_path,
        "flow_version_id": run.flow_version_id,
        "topic_slug": run.topic_slug,
        "queue_item_id": run.queue_item_id,
        "topic_queue_snapshot_id": run.topic_queue_snapshot_id,
        "selected_model": run.selected_model,
        "selected_puller": run.selected_puller,
        "run_status": run.status,
        "success": run.status == "completed",
        "accepted": final_accepted,
        "first_pass_accept": first_pass,
        "attempt_number": 1,
        "iteration_count": len(review_iterations) or len(iteration_rows),
        "review_count": review_count or None,
        "draft_count": draft_count or None,
        "initial_score": initial_score,
        "final_score": final_score,
        "highest_score": highest_score,
        "lowest_score": lowest_score,
        "score_change": score_change,
        "regression_count": regression_count,
        "no_progress_count": no_progress_count,
        "total_input_tokens": usage_stats.get("input_tokens"),
        "total_output_tokens": usage_stats.get("output_tokens"),
        "total_tokens": usage_stats.get("total_tokens"),
        "total_turns": usage_stats.get("total_turns"),
        "total_llm_calls": usage_stats.get("llm_calls"),
        "total_duration_ms": llm_duration,
        "wall_clock_duration_ms": wall_clock,
        "estimated_cost_usd": usage_stats.get("estimated_cost_usd"),
        "termination_reason": _infer_termination_reason(run, final_accepted=final_accepted),
        "error_message": run.error,
        "telemetry_warning_count": warning_count,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "final_article_text": final_article_text,
    }


def _delete_dependent_telemetry(db: Session, run_id: str) -> None:
    db.query(ReviewIssueTelemetry).filter_by(run_id=run_id).delete()
    db.query(CriterionTelemetry).filter_by(run_id=run_id).delete()
    db.query(IterationTelemetry).filter_by(run_id=run_id).delete()


def capture_run_telemetry(db: Session, run_id: str) -> RunTelemetry | None:
    run = db.query(FactoryRun).filter_by(run_id=run_id).first()
    if run is None:
        return None
    if run.status not in TERMINAL_STATUSES:
        logger.debug("Skipping telemetry for non-terminal run %s (%s)", run_id, run.status)
        return None

    flow = _load_flow_for_run(db, run)
    roles = resolve_flow_roles(flow) if flow else FlowRoleConfig(
        gate_step_key=None,
        producer_step_keys=[],
        ordered_step_keys=[],
    )
    step_records = _records_from_run(db, run)
    execution_lookup = _execution_lookup(db, run.run_id)

    iteration_rows, criterion_rows, issue_rows, warning_count = _aggregate_iteration_metrics(
        roles=roles,
        step_records=step_records,
        execution_lookup=execution_lookup,
        attempt_number=1,
    )
    run_payload = _compute_run_level(
        run=run,
        roles=roles,
        step_records=step_records,
        iteration_rows=iteration_rows,
        warning_count=warning_count,
        flow=flow,
        db=db,
    )

    existing = db.query(RunTelemetry).filter_by(run_id=run_id).first()
    if existing is None:
        existing = RunTelemetry(run_id=run_id)
        db.add(existing)

    for key, value in run_payload.items():
        setattr(existing, key, value)
    existing.updated_at = datetime.now(timezone.utc)

    _delete_dependent_telemetry(db, run_id)
    for row in iteration_rows:
        row["run_id"] = run_id
        db.add(IterationTelemetry(**row))
    for row in criterion_rows:
        row["run_id"] = run_id
        db.add(CriterionTelemetry(**row))
    for row in issue_rows:
        row["run_id"] = run_id
        db.add(ReviewIssueTelemetry(**row))

    db.commit()
    db.refresh(existing)
    return existing


def capture_run_telemetry_safe(db: Session, run_id: str) -> None:
    try:
        capture_run_telemetry(db, run_id)
    except Exception:
        logger.exception("Telemetry capture failed for run %s", run_id)


def rebuild_run_telemetry(db: Session, run_id: str) -> RunTelemetry | None:
    return capture_run_telemetry(db, run_id)


def rebuild_flow_telemetry(db: Session, flow_path: str, flow_version_id: int) -> dict[str, int]:
    runs = (
        db.query(FactoryRun)
        .filter(
            FactoryRun.flow_path == flow_path,
            FactoryRun.flow_version_id == flow_version_id,
            FactoryRun.status.in_(TERMINAL_STATUSES),
        )
        .order_by(FactoryRun.started_at.asc())
        .all()
    )
    parsed = 0
    warnings = 0
    failed = 0
    skipped = 0
    for run in runs:
        try:
            row = capture_run_telemetry(db, run.run_id)
            if row is None:
                skipped += 1
                continue
            parsed += 1
            warnings += int(row.telemetry_warning_count or 0)
        except Exception:
            failed += 1
            logger.exception("Failed rebuilding telemetry for %s", run.run_id)
    return {
        "parsed": parsed,
        "skipped": skipped,
        "warnings": warnings,
        "failed": failed,
        "total": len(runs),
    }


def get_flow_telemetry_rows(
    db: Session,
    flow_path: str,
    flow_version_id: int,
) -> list[RunTelemetry]:
    return (
        db.query(RunTelemetry)
        .filter_by(flow_path=flow_path, flow_version_id=flow_version_id)
        .order_by(RunTelemetry.started_at.asc())
        .all()
    )


def list_flow_telemetry_summary(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    model: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    query = db.query(RunTelemetry).filter_by(flow_path=flow_path, flow_version_id=flow_version_id)
    if status:
        query = query.filter(RunTelemetry.run_status == status)
    if model:
        query = query.filter(RunTelemetry.selected_model == model)
    total = query.count()
    rows = (
        query.order_by(RunTelemetry.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [
        {
            "run_id": row.run_id,
            "topic_slug": row.topic_slug,
            "model": row.selected_model,
            "accepted": row.accepted,
            "iteration_count": row.iteration_count,
            "initial_score": row.initial_score,
            "final_score": row.final_score,
            "total_tokens": row.total_tokens,
            "total_duration_ms": row.total_duration_ms,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in rows
    ]
    return total, items
