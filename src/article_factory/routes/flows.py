from __future__ import annotations

from pydantic import BaseModel, Field

from article_factory.services.flow_schema import FlowDefinition, flow_from_dict, flow_to_dict
from article_factory.services.flow_storage import (
    create_flow,
    create_flow_from_template,
    create_folder,
    delete_flow,
    delete_folder,
    duplicate_flow,
    export_flow,
    import_flow,
    list_folder_flows,
    list_templates,
    list_tree,
    move_flow,
    normalize_flow_rel_path,
    read_flow,
    write_flow,
)
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from article_factory.db import get_db
from article_factory.routes.admin import require_api_key

router = APIRouter(prefix="/api/flows", dependencies=[Depends(require_api_key)])


class CreateFlowBody(BaseModel):
    folder: str = ""
    slug: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    step_count: int = Field(..., ge=1, le=20)


class DuplicateFlowBody(BaseModel):
    path: str = Field(..., min_length=1)
    slug: str | None = None
    display_name: str | None = None


class MoveFlowBody(BaseModel):
    path: str = Field(..., min_length=1)
    folder: str = ""
    slug: str | None = None


class CreateFolderBody(BaseModel):
    path: str = Field(..., min_length=1)


class SaveFlowBody(BaseModel):
    flow: dict


class ImportFlowBody(BaseModel):
    folder: str = ""
    slug: str | None = None
    flow: dict
    overwrite: bool = False


class FromTemplateBody(BaseModel):
    template_path: str = Field(..., min_length=1)
    folder: str = ""
    slug: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)


@router.get("/templates")
def get_flow_templates() -> dict:
    return {"templates": list_templates()}


@router.post("/from-template")
def post_create_from_template(body: FromTemplateBody) -> dict:
    try:
        rel_path, flow = create_flow_from_template(
            template_path=body.template_path,
            folder=body.folder,
            slug=body.slug,
            display_name=body.display_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Template not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Flow already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": rel_path, "flow": flow_to_dict(flow)}


@router.get("/export")
def get_export_flow(path: str = Query(..., min_length=1)) -> dict:
    try:
        return export_flow(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Flow not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import")
def post_import_flow(body: ImportFlowBody) -> dict:
    try:
        flow = flow_from_dict(body.flow)
        rel_path = import_flow(
            flow,
            folder=body.folder,
            slug=body.slug,
            overwrite=body.overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Flow already exists") from exc
    saved = read_flow(rel_path)
    return {"path": rel_path, "flow": flow_to_dict(saved)}


@router.get("/list")
def get_flow_list(path: str = Query(default="")) -> dict:
    try:
        return {"flows": list_folder_flows(path)}
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/duplicate")
def post_duplicate_flow(body: DuplicateFlowBody) -> dict:
    try:
        rel_path, flow = duplicate_flow(body.path, slug=body.slug, display_name=body.display_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Flow not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Flow already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": rel_path, "flow": flow_to_dict(flow)}


@router.post("/move")
def post_move_flow(body: MoveFlowBody, db: Session = Depends(get_db)) -> dict:
    from article_factory.models import TopicQueueItem
    from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

    try:
        rel_path, flow = move_flow(body.path, folder=body.folder, slug=body.slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Flow not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="A flow already exists at that location") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    old_path = normalize_flow_rel_path(body.path)
    runtime = load_runtime_settings(db)
    if runtime.default_flow_path == old_path:
        update_factory_settings(
            db,
            {
                "control_plane_url": runtime.control_plane_url,
                "cms_url": runtime.cms_url,
                "cms_api_key": runtime.cms_api_key,
                "default_puller": runtime.default_puller,
                "default_model": runtime.default_model,
                "default_flow_path": rel_path,
            },
        )
    for item in db.query(TopicQueueItem).filter_by(flow_path=old_path, status="queued").all():
        item.flow_path = rel_path
    db.commit()

    return {"path": rel_path, "flow": flow_to_dict(flow), "moved_from": old_path}


@router.get("/tree")
def get_flow_tree(path: str = Query(default="")) -> dict:
    try:
        return list_tree(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/file")
def get_flow_file(path: str = Query(..., min_length=1)) -> dict:
    try:
        flow = read_flow(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Flow not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": path, "flow": flow_to_dict(flow)}


@router.put("/file")
def put_flow_file(path: str = Query(..., min_length=1), body: SaveFlowBody = Body(...)) -> dict:
    try:
        flow = flow_from_dict(body.flow)
        saved = write_flow(path, flow)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": path, "flow": flow_to_dict(saved)}


@router.post("/create")
def post_create_flow(body: CreateFlowBody) -> dict:
    try:
        rel_path, flow = create_flow(
            folder=body.folder,
            slug=body.slug,
            display_name=body.display_name,
            step_count=body.step_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Flow already exists") from exc
    return {"path": rel_path, "flow": flow_to_dict(flow)}


@router.post("/folders")
def post_create_folder(body: CreateFolderBody) -> dict:
    try:
        return create_folder(body.path)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Folder already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/folders")
def remove_folder(path: str = Query(..., min_length=1)) -> dict:
    try:
        delete_folder(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Folder not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/file")
def remove_flow_file(path: str = Query(..., min_length=1)) -> dict:
    try:
        delete_flow(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Flow not found") from exc
    return {"ok": True}
