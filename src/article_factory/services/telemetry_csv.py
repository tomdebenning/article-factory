"""CSV export helpers for run telemetry."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from article_factory.models import CriterionTelemetry, IterationTelemetry, RunTelemetry
from article_factory.services.review_parser import CRITERION_SPECS

DEFAULT_ITERATION_COLUMNS = 11

BASE_COLUMNS = [
    "flow_path",
    "flow_version_id",
    "run_id",
    "topic_slug",
    "queue_item_id",
    "topic_queue_snapshot_id",
    "attempt_number",
    "model",
    "puller",
    "run_status",
    "success",
    "accepted",
    "first_pass_accept",
    "iteration_count",
    "review_count",
    "draft_count",
    "initial_score",
    "final_score",
    "highest_score",
    "lowest_score",
    "score_change",
    "regression_count",
    "no_progress_count",
    "total_input_tokens",
    "total_output_tokens",
    "total_tokens",
    "total_turns",
    "total_llm_calls",
    "llm_duration_ms",
    "llm_duration_seconds",
    "wall_clock_duration_ms",
    "wall_clock_duration_seconds",
    "estimated_cost_usd",
    "termination_reason",
    "error_message",
    "started_at",
    "finished_at",
    "telemetry_warning_count",
]

FINAL_CRITERION_COLUMNS = [
    "final_accuracy_score",
    "final_accuracy_max",
    "final_organization_score",
    "final_organization_max",
    "final_writing_quality_score",
    "final_writing_quality_max",
    "final_depth_specificity_score",
    "final_depth_specificity_max",
    "final_reader_engagement_score",
    "final_reader_engagement_max",
    "final_grammar_mechanics_score",
    "final_grammar_mechanics_max",
]

CRITERION_KEY_TO_FINAL_PREFIX = {
    "accuracy_verifiable_facts": "final_accuracy",
    "organization_flow": "final_organization",
    "writing_quality": "final_writing_quality",
    "depth_specificity": "final_depth_specificity",
    "reader_engagement": "final_reader_engagement",
    "grammar_mechanics": "final_grammar_mechanics",
}


def _sanitize_filename_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return token or "flow"


def telemetry_export_filename(flow_path: str, flow_version_id: int) -> str:
    stem = _sanitize_filename_token(flow_path.replace(".flow.json", "").replace("/", "-"))
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"telemetry-{stem}-v{flow_version_id}-{date}.csv"


def _csv_safe(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def _iteration_columns(limit: int) -> list[str]:
    return [f"iteration_{index}_score" for index in range(1, limit + 1)]


def csv_headers(iteration_column_limit: int = DEFAULT_ITERATION_COLUMNS) -> list[str]:
    return (
        BASE_COLUMNS
        + _iteration_columns(iteration_column_limit)
        + ["iteration_scores_json", "iteration_verdicts_json"]
        + FINAL_CRITERION_COLUMNS
        + ["criterion_scores_json"]
    )


def _load_iteration_data(db: Session, run_id: str) -> tuple[list[IterationTelemetry], list[CriterionTelemetry]]:
    iterations = (
        db.query(IterationTelemetry)
        .filter_by(run_id=run_id)
        .order_by(IterationTelemetry.iteration_number.asc())
        .all()
    )
    criteria = (
        db.query(CriterionTelemetry)
        .filter_by(run_id=run_id)
        .order_by(CriterionTelemetry.iteration_number.asc(), CriterionTelemetry.criterion_key.asc())
        .all()
    )
    return iterations, criteria


def _row_dict(
    db: Session,
    row: RunTelemetry,
    *,
    iteration_column_limit: int = DEFAULT_ITERATION_COLUMNS,
) -> dict[str, Any]:
    iterations, criteria = _load_iteration_data(db, row.run_id)
    scores = [item.total_score for item in iterations if item.reviewer_step_key]
    verdicts = [item.verdict or "" for item in iterations if item.reviewer_step_key]

    payload: dict[str, Any] = {
        "flow_path": row.flow_path,
        "flow_version_id": row.flow_version_id,
        "run_id": row.run_id,
        "topic_slug": row.topic_slug,
        "queue_item_id": row.queue_item_id,
        "topic_queue_snapshot_id": row.topic_queue_snapshot_id,
        "attempt_number": row.attempt_number,
        "model": row.selected_model,
        "puller": row.selected_puller,
        "run_status": row.run_status,
        "success": row.success,
        "accepted": row.accepted,
        "first_pass_accept": row.first_pass_accept,
        "iteration_count": row.iteration_count,
        "review_count": row.review_count,
        "draft_count": row.draft_count,
        "initial_score": row.initial_score,
        "final_score": row.final_score,
        "highest_score": row.highest_score,
        "lowest_score": row.lowest_score,
        "score_change": row.score_change,
        "regression_count": row.regression_count,
        "no_progress_count": row.no_progress_count,
        "total_input_tokens": row.total_input_tokens,
        "total_output_tokens": row.total_output_tokens,
        "total_tokens": row.total_tokens,
        "total_turns": row.total_turns,
        "total_llm_calls": row.total_llm_calls,
        "llm_duration_ms": row.total_duration_ms,
        "llm_duration_seconds": (row.total_duration_ms / 1000.0) if row.total_duration_ms is not None else None,
        "wall_clock_duration_ms": row.wall_clock_duration_ms,
        "wall_clock_duration_seconds": (
            row.wall_clock_duration_ms / 1000.0 if row.wall_clock_duration_ms is not None else None
        ),
        "estimated_cost_usd": row.estimated_cost_usd,
        "termination_reason": row.termination_reason,
        "error_message": row.error_message,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "telemetry_warning_count": row.telemetry_warning_count,
        "iteration_scores_json": json.dumps(scores, separators=(",", ":")),
        "iteration_verdicts_json": json.dumps(verdicts, separators=(",", ":")),
    }

    for index in range(1, iteration_column_limit + 1):
        payload[f"iteration_{index}_score"] = scores[index - 1] if index <= len(scores) else None

    final_iteration = iterations[-1].iteration_number if iterations else None
    final_criteria = [c for c in criteria if c.iteration_number == final_iteration] if final_iteration else []
    for key, _label, _max in CRITERION_SPECS:
        prefix = CRITERION_KEY_TO_FINAL_PREFIX[key]
        match = next((c for c in final_criteria if c.criterion_key == key), None)
        payload[f"{prefix}_score"] = match.score if match else None
        payload[f"{prefix}_max"] = match.max_score if match else None

    history: dict[str, list[int | None]] = {key: [] for key, _label, _max in CRITERION_SPECS}
    for item in criteria:
        history.setdefault(item.criterion_key, []).append(item.score)
    payload["criterion_scores_json"] = json.dumps(history, separators=(",", ":"))
    return payload


def build_telemetry_csv(
    db: Session,
    rows: Iterable[RunTelemetry],
    *,
    iteration_column_limit: int = DEFAULT_ITERATION_COLUMNS,
) -> str:
    headers = csv_headers(iteration_column_limit)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        payload = _row_dict(db, row, iteration_column_limit=iteration_column_limit)
        writer.writerow({key: _csv_safe(payload.get(key)) for key in headers})
    return buffer.getvalue()
