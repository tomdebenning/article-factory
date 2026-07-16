from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun
from article_factory.services.run_turn_metrics import gate_step_key_for_run, review_cycles_for_run
from article_factory.services.step_trace import list_step_executions


def outcome_cycle_for_run(run: FactoryRun, db: Session) -> int | None:
    """1-based writer–review cycle when the run finished (success or failure)."""
    if run.status == "completed":
        cycles = review_cycles_for_run(run, db)
        return cycles if cycles > 0 else None

    if run.status not in ("failed", "cancelled"):
        return None

    gate_key = gate_step_key_for_run(run, db)
    executions = list_step_executions(db, run.run_id)
    if not executions:
        return 1

    completed_reviews = sum(
        1 for row in executions if gate_key and row.step_key == gate_key and row.status == "completed"
    )
    last = executions[-1]
    if gate_key and last.step_key == gate_key:
        return max(completed_reviews, 1)
    return completed_reviews + 1 if completed_reviews else 1


def build_turn_outcome_charts(runs: list[FactoryRun], db: Session) -> dict[str, Any]:
    success_counts: dict[int, int] = {}
    failure_counts: dict[int, int] = {}

    for run in runs:
        cycle = outcome_cycle_for_run(run, db)
        if cycle is None:
            continue
        if run.status == "completed":
            success_counts[cycle] = success_counts.get(cycle, 0) + 1
        elif run.status in ("failed", "cancelled"):
            failure_counts[cycle] = failure_counts.get(cycle, 0) + 1

    all_turns = sorted(set(success_counts) | set(failure_counts))

    def _rows(counts: dict[int, int]) -> list[dict[str, Any]]:
        if not all_turns:
            return [
                {"turn": turn, "count": counts.get(turn, 0)}
                for turn in sorted(counts)
            ]
        return [{"turn": turn, "count": counts.get(turn, 0)} for turn in all_turns]

    success_rows = _rows(success_counts)
    failure_rows = _rows(failure_counts)
    success_total = sum(row["count"] for row in success_rows)
    failure_total = sum(row["count"] for row in failure_rows)

    return {
        "success_by_turn": success_rows,
        "failure_by_turn": failure_rows,
        "success_total": success_total,
        "failure_total": failure_total,
    }
