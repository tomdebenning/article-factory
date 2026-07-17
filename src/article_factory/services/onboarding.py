"""First-shift onboarding signals for The Newsroom."""

from __future__ import annotations

from sqlalchemy.orm import Session

from article_factory.models import CompletedArticle, ShiftDeskSlot, ShiftPlan
from article_factory.services.flow_storage import TEMPLATES_FOLDER, flows_root, is_flow_file

DEFAULT_DESK_ONLY = {"sports/standard-4-step.flow.json"}


def has_completed_first_shift(db: Session) -> bool:
    published = db.query(CompletedArticle.id).limit(1).first()
    if published is not None:
        return True
    completed = db.query(ShiftPlan.id).filter_by(status="complete").limit(1).first()
    return completed is not None


def has_user_desk() -> bool:
    root = flows_root()
    for path in root.rglob("*.flow.json"):
        if not is_flow_file(path):
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(f"{TEMPLATES_FOLDER}/"):
            continue
        if rel not in DEFAULT_DESK_ONLY:
            return True
    return False


def has_shift_roster(db: Session) -> bool:
    return db.query(ShiftDeskSlot.id).limit(1).first() is not None


def has_activated_shift(db: Session) -> bool:
    row = (
        db.query(ShiftPlan.id)
        .filter(ShiftPlan.status.in_(("active", "complete")))
        .limit(1)
        .first()
    )
    return row is not None


def morning_shift_onboarding(db: Session, *, setup_complete: bool) -> dict:
    done = has_completed_first_shift(db)
    desk_ok = has_user_desk()
    plan_ok = has_shift_roster(db)
    activate_ok = done or has_activated_shift(db)
    return {
        "show_wizard": not done,
        "completed": done,
        "steps": [
            {
                "id": "settings",
                "label": "Configure integrations",
                "description": "Set control plane, model, and Edition publish settings.",
                "action_path": "/settings",
                "ok": setup_complete,
            },
            {
                "id": "desk",
                "label": "Create a desk from a template",
                "description": "Start with Sports, Business, Tech, or AI News.",
                "action_path": "/flows/new",
                "ok": desk_ok,
            },
            {
                "id": "plan",
                "label": "Plan the Morning Shift",
                "description": "Staff desks and load assignments for the next morning window.",
                "action_path": "/start-flows",
                "ok": plan_ok,
            },
            {
                "id": "activate",
                "label": "Activate the shift",
                "description": "Start dispatch when the roster is ready.",
                "action_path": "/shifts",
                "ok": activate_ok,
            },
        ],
    }
