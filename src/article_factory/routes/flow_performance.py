from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.routes.admin import require_api_key
from article_factory.services.flow_performance import aggregate_performance, list_topic_queues_for_flow
from article_factory.services.flow_storage import normalize_flow_rel_path
from article_factory.services.flow_versions import (
    create_flow_version,
    diff_flow_versions,
    get_flow_version,
    list_flow_versions,
    version_to_dict,
)
from article_factory.services.prompt_coach import analysis_to_dict, analyze_flow_performance

router = APIRouter(prefix="/api/flows", dependencies=[Depends(require_api_key)])


class CreateFlowVersionBody(BaseModel):
    path: str
    message: str = ""


class AnalyzeFlowBody(BaseModel):
    path: str
    flow_version_id: int | None = None
    topic_queue_snapshot_id: int | None = None
    selected_model: str = ""


@router.post("/versions")
def post_create_flow_version(body: CreateFlowVersionBody, db: Session = Depends(get_db)) -> dict:
    path = normalize_flow_rel_path(body.path)
    try:
        row = create_flow_version(db, path, message=body.message)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"version": version_to_dict(row)}


@router.get("/versions")
def get_flow_versions(path: str = Query(..., min_length=1), db: Session = Depends(get_db)) -> dict:
    flow_path = normalize_flow_rel_path(path)
    rows = list_flow_versions(db, flow_path)
    versions = []
    for index, row in enumerate(rows):
        payload = version_to_dict(row)
        if index + 1 < len(rows):
            payload["changes_from_previous"] = diff_flow_versions(
                rows[index + 1].flow_content or {},
                row.flow_content or {},
            )
        else:
            payload["changes_from_previous"] = []
        versions.append(payload)
    return {"flow_path": flow_path, "versions": versions}


@router.get("/versions/detail")
def get_flow_version_detail(version_id: int = Query(...), db: Session = Depends(get_db)) -> dict:
    row = get_flow_version(db, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Flow version not found")
    return {"version": {**version_to_dict(row), "flow_content": row.flow_content}}


@router.get("/performance")
def get_flow_performance(
    path: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    flow_version_id: int | None = Query(default=None),
    topic_queue_snapshot_id: int | None = Query(default=None),
    selected_model: str = Query(default=""),
) -> dict:
    flow_path = normalize_flow_rel_path(path)
    return aggregate_performance(
        db,
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        topic_queue_snapshot_id=topic_queue_snapshot_id,
        selected_model=selected_model.strip() or None,
    )


@router.get("/topic-queues")
def get_flow_topic_queues(path: str = Query(..., min_length=1), db: Session = Depends(get_db)) -> dict:
    flow_path = normalize_flow_rel_path(path)
    return {"topic_queues": list_topic_queues_for_flow(db, flow_path)}


@router.post("/analyze")
def post_analyze_flow(body: AnalyzeFlowBody, db: Session = Depends(get_db)) -> dict:
    flow_path = normalize_flow_rel_path(body.path)
    row = analyze_flow_performance(
        db,
        flow_path=flow_path,
        flow_version_id=body.flow_version_id,
        topic_queue_snapshot_id=body.topic_queue_snapshot_id,
        selected_model=body.selected_model.strip() or None,
    )
    return {"analysis": analysis_to_dict(row)}
