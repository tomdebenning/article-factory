from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from article_factory.config import settings
from article_factory.db import get_db
from article_factory.routes.admin import require_api_key, require_api_key_header_or_query
from article_factory.services.flow_storage import normalize_flow_rel_path
from article_factory.services.flow_versions import get_flow_version
from article_factory.services.telemetry import (
    get_flow_telemetry_rows,
    list_flow_telemetry_summary,
    rebuild_flow_telemetry,
)
from article_factory.services.telemetry_csv import (
    build_telemetry_csv,
    telemetry_export_filename,
)

router = APIRouter(prefix="/api/flows")


@router.get("/telemetry", dependencies=[Depends(require_api_key)])
def get_flow_telemetry(
    path: str = Query(..., min_length=1),
    flow_version_id: int = Query(...),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    model: str | None = Query(default=None),
) -> dict:
    flow_path = normalize_flow_rel_path(path)
    version = get_flow_version(db, flow_version_id)
    if version is None or version.flow_path != flow_path:
        raise HTTPException(status_code=404, detail="Flow version not found for this flow path")
    total, items = list_flow_telemetry_summary(
        db,
        flow_path=flow_path,
        flow_version_id=flow_version_id,
        limit=limit,
        offset=offset,
        status=status.strip() if status else None,
        model=model.strip() if model else None,
    )
    return {
        "flow_path": flow_path,
        "flow_version_id": flow_version_id,
        "total": total,
        "items": items,
    }


@router.get("/telemetry/export", dependencies=[Depends(require_api_key_header_or_query)])
def export_flow_telemetry_csv(
    path: str = Query(..., min_length=1),
    flow_version_id: int = Query(...),
    db: Session = Depends(get_db),
) -> Response:
    flow_path = normalize_flow_rel_path(path)
    version = get_flow_version(db, flow_version_id)
    if version is None or version.flow_path != flow_path:
        raise HTTPException(status_code=404, detail="Flow version not found for this flow path")

    rows = get_flow_telemetry_rows(db, flow_path, flow_version_id)
    if not rows:
        rebuild_flow_telemetry(db, flow_path, flow_version_id)
        rows = get_flow_telemetry_rows(db, flow_path, flow_version_id)

    csv_text = build_telemetry_csv(
        db,
        rows,
        iteration_column_limit=settings.telemetry_csv_iteration_columns,
    )
    filename = telemetry_export_filename(flow_path, flow_version_id)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/telemetry/rebuild", dependencies=[Depends(require_api_key)])
def rebuild_flow_telemetry_endpoint(
    path: str = Query(..., min_length=1),
    flow_version_id: int = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    flow_path = normalize_flow_rel_path(path)
    version = get_flow_version(db, flow_version_id)
    if version is None or version.flow_path != flow_path:
        raise HTTPException(status_code=404, detail="Flow version not found for this flow path")
    return rebuild_flow_telemetry(db, flow_path, flow_version_id)
