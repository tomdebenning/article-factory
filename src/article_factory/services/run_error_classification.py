from __future__ import annotations

from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, RunErrorTag, StepExecution
from article_factory.services.verdict import Verdict, parse_verdict

ERROR_GROUPS: dict[str, str] = {
    "completed": "Completed successfully",
    "iteration_limit": "Exceeded iteration limit",
    "missing_verdict": "Review missing ACCEPT/REJECT",
    "puller_timeout": "Puller / dispatch timeout",
    "llm_error": "LLM / model error",
    "run_interrupted": "Run interrupted (factory restart)",
    "cancelled": "Cancelled",
    "failed_other": "Other failure",
    "running": "Still running",
    "queued": "Not started",
}


def _step_records(run: FactoryRun) -> list[dict[str, Any]]:
    manifest = run.manifest or {}
    pipeline = run.pipeline_state or {}
    records = list(manifest.get("step_stats") or manifest.get("steps") or [])
    if not records and pipeline:
        records = list(pipeline.get("step_records") or [])
    return records


def _detect_missing_verdict(run: FactoryRun) -> bool:
    if run.error and "missing VERDICT" in run.error:
        return True
    for record in _step_records(run):
        if str(record.get("step_key") or "") != "review":
            continue
        content = str(record.get("content") or record.get("response_content") or "")
        if content and parse_verdict(content) == Verdict.NONE:
            return True
    return False


def classify_run_error(run: FactoryRun, *, step_errors: list[str] | None = None) -> str:
    if run.status == "running":
        return "running"
    if run.status == "cancelled":
        return "cancelled"

    error_text = (run.error or "").lower()
    if run.status == "completed" and not run.error:
        return "completed"

    if "max flow iterations exceeded" in error_text:
        return "iteration_limit"
    if "missing verdict" in error_text:
        return "missing_verdict"
    if _detect_missing_verdict(run):
        return "missing_verdict"
    if "run interrupted" in error_text or "factory restarted" in error_text:
        return "run_interrupted"
    if any(
        token in error_text
        for token in (
            "no puller picked up",
            "timed out",
            "timeout",
            "did not pick up",
            "stopped heartbeating",
        )
    ):
        return "puller_timeout"
    if any(
        token in error_text
        for token in (
            "ollama_transport",
            "llm_transport",
            "llm_application",
            "llm error",
            "http 500",
            "http 502",
        )
    ):
        return "llm_error"

    for step_error in step_errors or []:
        lowered = step_error.lower()
        if any(token in lowered for token in ("ollama", "llm_", "transport_error", "http 5")):
            return "llm_error"
        if "timeout" in lowered or "puller" in lowered:
            return "puller_timeout"

    if run.status == "failed":
        return "failed_other"
    return "completed"


def error_group_label(group: str) -> str:
    return ERROR_GROUPS.get(group, group.replace("_", " ").title())


def load_manual_error_tags(db: Session, run_ids: list[str]) -> dict[str, RunErrorTag]:
    if not run_ids:
        return {}
    try:
        rows = db.query(RunErrorTag).filter(RunErrorTag.run_id.in_(run_ids)).all()
    except OperationalError:
        return {}
    return {row.run_id: row for row in rows}


def resolve_run_error_group(
    run: FactoryRun,
    *,
    manual_tags: dict[str, RunErrorTag] | None = None,
    step_errors: list[str] | None = None,
) -> dict[str, Any]:
    manual = (manual_tags or {}).get(run.run_id)
    auto_group = classify_run_error(run, step_errors=step_errors)
    group = manual.error_group if manual and manual.error_group else auto_group
    return {
        "error_group": group,
        "error_group_label": error_group_label(group),
        "auto_error_group": auto_group,
        "auto_error_group_label": error_group_label(auto_group),
        "manual_tag": manual.error_group if manual else None,
        "manual_note": manual.note if manual else None,
        "error_message": run.error,
    }


def step_errors_for_run(db: Session, run_id: str) -> list[str]:
    rows = db.query(StepExecution.error).filter_by(run_id=run_id).all()
    return [str(row[0]) for row in rows if row[0]]
