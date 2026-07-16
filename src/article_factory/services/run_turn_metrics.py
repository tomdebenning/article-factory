from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun
from article_factory.services.flow_roles import resolve_flow_roles
from article_factory.services.flow_schema import FlowDefinition
from article_factory.services.flow_storage import read_flow
from article_factory.services.flow_versions import get_flow_version, load_version_flow
from article_factory.services.step_trace import list_step_executions


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.mean(values))


def _gate_step_key_for_run(run: FactoryRun, db: Session | None = None) -> str | None:
    flow: FlowDefinition | None = None
    if db is not None and run.flow_version_id:
        version = get_flow_version(db, run.flow_version_id)
        if version:
            try:
                flow = load_version_flow(version)
            except Exception:
                flow = None
    if flow is None and run.flow_path:
        try:
            flow = read_flow(run.flow_path)
        except Exception:
            flow = None
    if flow is not None:
        return resolve_flow_roles(flow).gate_step_key

    steps = list((run.manifest or {}).get("step_stats") or (run.manifest or {}).get("steps") or [])
    keys = {str(step.get("step_key") or "") for step in steps}
    if "review" in keys:
        return "review"
    if "step_2" in keys:
        return "step_2"
    return None


def gate_step_key_for_run(run: FactoryRun, db: Session | None = None) -> str | None:
    return _gate_step_key_for_run(run, db)


def review_cycles_for_run(run: FactoryRun, db: Session | None = None) -> int:
    """How many times the gate/review step executed (writer–review loops)."""
    gate_key = _gate_step_key_for_run(run, db)
    steps = list((run.manifest or {}).get("step_stats") or (run.manifest or {}).get("steps") or [])
    if steps and gate_key:
        count = sum(1 for step in steps if str(step.get("step_key") or "") == gate_key)
        if count > 0:
            return count

    if db is not None and gate_key:
        count = sum(
            1
            for row in list_step_executions(db, run.run_id)
            if row.step_key == gate_key and row.status == "completed"
        )
        if count > 0:
            return count

    if db is not None and not gate_key:
        for candidate in ("review", "step_2"):
            count = sum(
                1
                for row in list_step_executions(db, run.run_id)
                if row.step_key == candidate and row.status == "completed"
            )
            if count > 0:
                return count

    production = (run.manifest or {}).get("production") or {}
    if production.get("iteration_count") is not None:
        return int(production["iteration_count"])
    return int(run.review_round or 0)


# Backwards-compatible alias
review_rounds_for_run = review_cycles_for_run


def step_turns_for_run(db: Session, run: FactoryRun) -> dict[str, Any]:
    executions = list_step_executions(db, run.run_id)
    per_step: dict[str, list[int]] = {}
    total = 0
    for row in executions:
        if row.turns is None:
            continue
        turns = int(row.turns)
        total += turns
        per_step.setdefault(row.step_key, []).append(turns)

    if not executions:
        for record in (run.manifest or {}).get("step_stats") or (run.manifest or {}).get("steps") or []:
            turns = record.get("turns")
            if turns is None:
                continue
            turns_int = int(turns)
            total += turns_int
            key = str(record.get("step_key") or "step")
            per_step.setdefault(key, []).append(turns_int)

    by_step_avg = {
        key: _mean([float(value) for value in values]) for key, values in per_step.items() if values
    }
    return {
        "total_step_turns": total,
        "by_step_avg_turns": by_step_avg,
    }


def turn_metrics_for_runs(runs: list[FactoryRun], db: Session) -> dict[str, Any]:
    completed = [run for run in runs if run.status == "completed"]
    review_cycles = [
        float(review_cycles_for_run(run, db))
        for run in completed
        if review_cycles_for_run(run, db) > 0
    ]
    step_turn_totals = [
        float(step_turns_for_run(db, run)["total_step_turns"])
        for run in completed
        if step_turns_for_run(db, run)["total_step_turns"] > 0
    ]
    return {
        "avg_review_rounds": _mean(review_cycles),
        "median_review_rounds": _median(review_cycles),
        "avg_step_turns": _mean(step_turn_totals),
        "median_step_turns": _median(step_turn_totals),
    }
