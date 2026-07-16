from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.routes.admin import require_api_key
from article_factory.services.flow_storage import normalize_flow_rel_path
from article_factory.services.flow_versions import get_flow_version, load_version_flow
from article_factory.services.prompt_improvement import (
    create_improvement_job,
    get_improvement_report,
    job_to_dict,
    list_improvement_jobs,
    report_to_dict,
    validate_improvement_request,
)
from article_factory.services.prompt_improvement_runner import prompt_improvement_runner
from article_factory.services.telemetry_ranking import MIN_RUNS_FOR_IMPROVEMENT

router = APIRouter(prefix="/api/flows", dependencies=[Depends(require_api_key)])


class StartPromptImprovementBody(BaseModel):
    path: str
    flow_version_id: int
    scope: str = Field(pattern="^(step|flow)$")
    target_step_key: str = ""
    selected_model: str = Field(min_length=1)
    selected_puller: str = Field(min_length=1)


@router.post("/prompt-improvement")
async def post_prompt_improvement(body: StartPromptImprovementBody, db: Session = Depends(get_db)) -> dict:
    flow_path = normalize_flow_rel_path(body.path)
    try:
        validate_improvement_request(
            db,
            flow_path=flow_path,
            flow_version_id=body.flow_version_id,
            selected_model=body.selected_model,
            selected_puller=body.selected_puller,
            scope=body.scope,
            target_step_key=body.target_step_key,
        )
        job = create_improvement_job(
            db,
            flow_path=flow_path,
            source_flow_version_id=body.flow_version_id,
            scope=body.scope,
            target_step_key=body.target_step_key,
            selected_model=body.selected_model,
            selected_puller=body.selected_puller,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    prompt_improvement_runner.enqueue(job.id)
    return {"job": job_to_dict(job)}


@router.get("/prompt-improvement/steps")
def get_prompt_improvement_steps(
    path: str = Query(..., min_length=1),
    flow_version_id: int = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    flow_path = normalize_flow_rel_path(path)
    version = get_flow_version(db, flow_version_id)
    if version is None or version.flow_path != flow_path:
        raise HTTPException(status_code=404, detail="Flow version not found for this flow path")
    flow = load_version_flow(version)
    steps = []
    for step in flow.steps:
        if not (step.system_prompt or "").strip() and not (step.user_prompt_template or "").strip():
            continue
        steps.append(
            {
                "step_key": step.step_key,
                "label": step.label,
                "has_system_prompt": bool((step.system_prompt or "").strip()),
                "has_user_prompt_template": bool((step.user_prompt_template or "").strip()),
            }
        )
    return {
        "flow_path": flow_path,
        "flow_version_id": flow_version_id,
        "min_completed_runs": MIN_RUNS_FOR_IMPROVEMENT,
        "steps": steps,
    }


@router.get("/prompt-improvement/reports/{report_id}")
def get_prompt_improvement_report(report_id: int, db: Session = Depends(get_db)) -> dict:
    row = get_improvement_report(db, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Prompt improvement report not found")
    return {"report": report_to_dict(row)}


@router.get("/prompt-improvement/{job_id}")
def get_prompt_improvement_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    from article_factory.models import PromptImprovementJob

    row = db.get(PromptImprovementJob, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Prompt improvement job not found")
    return {"job": job_to_dict(row)}


@router.get("/prompt-improvement")
def get_prompt_improvement_jobs(
    path: str = Query(..., min_length=1),
    flow_version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    flow_path = normalize_flow_rel_path(path)
    rows = list_improvement_jobs(db, flow_path=flow_path, flow_version_id=flow_version_id)
    return {"jobs": [job_to_dict(row) for row in rows]}
