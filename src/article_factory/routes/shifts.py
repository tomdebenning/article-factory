from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.orchestrator.runner import factory_loop
from article_factory.routes.admin import require_api_key
from article_factory.schemas import (
    ShiftAssignmentsBody,
    ShiftDeskSlotBody,
    ShiftPlanEnsureBody,
    ShiftPlanSaveBody,
    ShiftPlanSettingsBody,
    RosterReviewBody,
)
from article_factory.services.assignment_desk import (
    approve_roster,
    reject_ai_suggestions,
    update_roster_assignments,
)
from article_factory.services.queue_presets import write_queue_preset
from article_factory.services.runtime_settings import update_factory_settings
from article_factory.services.shift_plans import (
    activate_shift_plan,
    add_desk_slot,
    get_or_create_shift_plan,
    get_shift_plan,
    list_assignments_for_desk,
    list_shift_board,
    replace_desk_assignments,
    shift_plan_payload,
    update_shift_plan_settings,
)
from article_factory.services.shift_windows import today_and_tomorrow_shift_windows

router = APIRouter(prefix="/api/shifts", dependencies=[Depends(require_api_key)])


def _window_from_key(window_key: str):
    for window in today_and_tomorrow_shift_windows():
        if window.window_key == window_key:
            return window
    raise HTTPException(status_code=400, detail="Invalid shift window key")


@router.get("/board")
def get_shift_board(db: Session = Depends(get_db)) -> dict:
    return {"windows": list_shift_board(db)}


@router.post("/plans/ensure")
def ensure_shift_plan(body: ShiftPlanEnsureBody, db: Session = Depends(get_db)) -> dict:
    window = _window_from_key(body.window_key.strip())
    plan = get_or_create_shift_plan(db, window)
    db.commit()
    db.refresh(plan)
    return {"plan": shift_plan_payload(db, plan)}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        plan = get_shift_plan(db, plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"plan": shift_plan_payload(db, plan)}


@router.post("/plans/{plan_id}/desks")
def post_desk_slot(plan_id: int, body: ShiftDeskSlotBody, db: Session = Depends(get_db)) -> dict:
    try:
        slot = add_desk_slot(
            db,
            plan_id=plan_id,
            desk_path=body.desk_path,
            topic_slug=body.topic_slug,
            name=body.name,
            flow_version_id=body.flow_version_id,
            reporter_selection_mode=body.reporter_selection_mode,
        )
        db.commit()
        db.refresh(slot)
        plan = get_shift_plan(db, plan_id)
        return {"plan": shift_plan_payload(db, plan)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/plans/{plan_id}/desks/{desk_id}/assignments")
def put_desk_assignments(
    plan_id: int,
    desk_id: int,
    body: ShiftAssignmentsBody,
    db: Session = Depends(get_db),
) -> dict:
    try:
        get_shift_plan(db, plan_id)
        created = replace_desk_assignments(
            db,
            desk_slot_id=desk_id,
            prompts=body.prompts,
            priority=body.priority,
        )
        db.commit()
        return {"count": len(created), "assignments": list_assignments_for_desk(db, desk_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/plans/{plan_id}/settings")
def patch_plan_settings(
    plan_id: int,
    body: ShiftPlanSettingsBody,
    db: Session = Depends(get_db),
) -> dict:
    try:
        plan = update_shift_plan_settings(db, plan_id, default_model=body.default_model)
        db.commit()
        return {"plan": shift_plan_payload(db, plan)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/activate")
def post_activate_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        plan = activate_shift_plan(db, plan_id)
        db.commit()
        factory_loop.request_dispatch()
        return {"plan": shift_plan_payload(db, plan), "message": "Shift activated."}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/save")
def save_shift_plan(body: ShiftPlanSaveBody, db: Session = Depends(get_db)) -> dict:
    window = _window_from_key(body.window_key.strip())
    if not body.desks:
        raise HTTPException(status_code=400, detail="Add at least one desk.")
    model = body.default_model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="Select a model for this shift.")

    update_factory_settings(db, {"default_model": model})
    plan = get_or_create_shift_plan(db, window)
    if plan.status == "active":
        raise HTTPException(status_code=400, detail="Cannot edit an active shift from this form.")
    if plan.status == "complete":
        raise HTTPException(status_code=400, detail="Shift is complete — plan the next window instead.")

    update_shift_plan_settings(db, plan.id, default_model=model)

    from article_factory.models import ShiftAssignment, ShiftDeskSlot

    db.query(ShiftAssignment).filter(
        ShiftAssignment.shift_desk_slot_id.in_(
            db.query(ShiftDeskSlot.id).filter_by(shift_plan_id=plan.id)
        )
    ).delete(synchronize_session=False)
    db.query(ShiftDeskSlot).filter_by(shift_plan_id=plan.id).delete(synchronize_session=False)
    db.flush()

    all_prompts: list[str] = []
    for index, desk_body in enumerate(body.desks):
        slot = add_desk_slot(
            db,
            plan_id=plan.id,
            desk_path=desk_body.desk_path,
            topic_slug=desk_body.topic_slug,
            name=desk_body.name,
            flow_version_id=desk_body.flow_version_id,
            reporter_selection_mode=desk_body.reporter_selection_mode,
        )
        prompts = body.assignments_by_desk_index.get(str(index), [])
        locked_flags = body.locked_by_desk_index.get(str(index), [])
        if prompts:
            replace_desk_assignments(
                db,
                desk_slot_id=slot.id,
                prompts=prompts,
                locked_flags=locked_flags or None,
            )
            all_prompts.extend([line.strip() for line in prompts if line.strip()])

    if not all_prompts and not body.desks:
        raise HTTPException(status_code=400, detail="Add at least one desk.")

    preset = None
    if body.save_preset and body.desks and all_prompts:
        first = body.desks[0]
        preset = write_queue_preset(
            db,
            {
                "name": body.preset_name or f"{window.shift_key.title()} roster",
                "slug": body.preset_slug,
                "topic_slug": first.topic_slug,
                "flow_path": first.desk_path,
                "default_model": model,
                "topics": all_prompts,
            },
        )

    db.commit()
    plan = get_shift_plan(db, plan.id)
    return {
        "plan": shift_plan_payload(db, plan),
        "preset": preset,
        "message": (
            f"Saved shift plan with {len(all_prompts)} assignment(s)."
            if all_prompts
            else "Saved shift plan — assignments will be generated at T-15."
        ),
    }


@router.patch("/plans/{plan_id}/roster")
def patch_roster_review(
    plan_id: int,
    body: RosterReviewBody,
    db: Session = Depends(get_db),
) -> dict:
    try:
        get_shift_plan(db, plan_id)
        if body.assignments:
            update_roster_assignments(
                db,
                plan_id=plan_id,
                updates=[item.model_dump() for item in body.assignments],
            )
        db.commit()
        plan = get_shift_plan(db, plan_id)
        return {"plan": shift_plan_payload(db, plan)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/roster/approve")
def post_roster_approve(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        plan = approve_roster(db, plan_id=plan_id)
        db.commit()
        return {"plan": shift_plan_payload(db, plan), "message": "Roster approved."}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/roster/reject-ai")
def post_roster_reject_ai(plan_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        get_shift_plan(db, plan_id)
        removed = reject_ai_suggestions(db, plan_id=plan_id)
        db.commit()
        plan = get_shift_plan(db, plan_id)
        return {
            "plan": shift_plan_payload(db, plan),
            "removed": removed,
            "message": f"Removed {removed} AI suggestion(s).",
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
