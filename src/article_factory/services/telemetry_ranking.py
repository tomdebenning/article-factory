"""Rank runs by composite telemetry quality for prompt-improvement examples."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import IterationTelemetry, RunTelemetry

MIN_RUNS_FOR_IMPROVEMENT = 10
EXAMPLE_FRACTION = 0.25


@dataclass(frozen=True)
class RankedRun:
    run_id: str
    composite_score: float
    bucket: str
    metrics: dict[str, Any]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def composite_quality_score(row: RunTelemetry, *, iteration_rows: list[IterationTelemetry]) -> float:
    final_score = row.final_score if row.final_score is not None else row.highest_score
    score_component = _clamp01((final_score or 0) / 100.0)

    iterations = row.iteration_count or len(iteration_rows) or 1
    iteration_component = _clamp01(1.0 - min(iterations, 10) / 10.0)

    open_issues = sum(int(item.not_fixed_issue_count or 0) for item in iteration_rows)
    issue_component = _clamp01(1.0 - min(open_issues, 20) / 20.0)

    first_pass_component = 1.0 if row.first_pass_accept else 0.0

    regressions = row.regression_count or 0
    regression_component = _clamp01(1.0 - min(regressions, 5) / 5.0)

    return (
        0.35 * score_component
        + 0.25 * iteration_component
        + 0.20 * issue_component
        + 0.10 * first_pass_component
        + 0.10 * regression_component
    )


def rank_runs_for_version(
    db: Session,
    *,
    flow_path: str,
    flow_version_id: int,
) -> list[RankedRun]:
    rows = (
        db.query(RunTelemetry)
        .filter_by(flow_path=flow_path, flow_version_id=flow_version_id, run_status="completed")
        .order_by(RunTelemetry.started_at.asc())
        .all()
    )
    ranked: list[tuple[RunTelemetry, float, list[IterationTelemetry]]] = []
    for row in rows:
        iterations = (
            db.query(IterationTelemetry)
            .filter_by(run_id=row.run_id)
            .order_by(IterationTelemetry.iteration_number.asc())
            .all()
        )
        score = composite_quality_score(row, iteration_rows=iterations)
        ranked.append((row, score, iterations))

    ranked.sort(key=lambda item: item[1], reverse=True)
    count = len(ranked)
    if count == 0:
        return []

    example_count = max(1, int(count * EXAMPLE_FRACTION))
    results: list[RankedRun] = []
    for index, (row, score, iterations) in enumerate(ranked):
        if index < example_count:
            bucket = "success"
        elif index >= count - example_count:
            bucket = "failure"
        else:
            bucket = "middle"
        results.append(
            RankedRun(
                run_id=row.run_id,
                composite_score=score,
                bucket=bucket,
                metrics={
                    "final_score": row.final_score,
                    "iteration_count": row.iteration_count,
                    "first_pass_accept": row.first_pass_accept,
                    "regression_count": row.regression_count,
                    "not_fixed_issues": sum(int(item.not_fixed_issue_count or 0) for item in iterations),
                },
            )
        )
    return results


def select_example_runs(ranked: list[RankedRun]) -> tuple[list[RankedRun], list[RankedRun]]:
    successes = [item for item in ranked if item.bucket == "success"]
    failures = [item for item in ranked if item.bucket == "failure"]
    return successes, failures
