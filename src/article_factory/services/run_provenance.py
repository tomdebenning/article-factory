"""Attach shift, desk, and reporter context to run manifests."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun, ShiftAssignment, ShiftDeskSlot, ShiftPlan
from article_factory.services.shift_windows import SHIFT_LABELS


def enrich_manifest_with_run_context(db: Session, run: FactoryRun, manifest: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(manifest)
    if (run.reporter_persona_slug or "").strip():
        enriched["reporter_persona_slug"] = run.reporter_persona_slug
    if (run.reporter_persona_name or "").strip():
        enriched["reporter_persona_name"] = run.reporter_persona_name
        enriched["reported_by"] = run.reporter_persona_name

    if run.shift_plan_id is not None:
        plan = db.get(ShiftPlan, run.shift_plan_id)
        if plan is not None:
            enriched["shift_plan_id"] = plan.id
            enriched["shift_key"] = plan.shift_key
            enriched["shift_label"] = SHIFT_LABELS.get(plan.shift_key, plan.shift_key)
            enriched["shift_window_starts_at"] = (
                plan.window_starts_at.isoformat() if plan.window_starts_at else None
            )

    if run.shift_assignment_id is not None:
        assignment = db.get(ShiftAssignment, run.shift_assignment_id)
        if assignment is not None:
            slot = db.get(ShiftDeskSlot, assignment.shift_desk_slot_id)
            if slot is not None:
                enriched["desk_name"] = slot.name or slot.desk_path
                enriched["desk_path"] = slot.desk_path
                if not enriched.get("reporter_persona_slug") and assignment.reporter_persona_slug:
                    enriched["reporter_persona_slug"] = assignment.reporter_persona_slug

    for step in enriched.get("steps") or enriched.get("step_stats") or []:
        if not isinstance(step, dict):
            continue
        step_key = str(step.get("step_key") or "")
        if step_key == "writer" or step.get("persona_name"):
            if run.reporter_persona_name and not step.get("persona_name"):
                step["persona_name"] = run.reporter_persona_name
            if run.reporter_persona_slug and not step.get("persona_slug"):
                step["persona_slug"] = run.reporter_persona_slug

    return enriched
