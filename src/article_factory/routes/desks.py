from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.db import get_db
from article_factory.orchestrator.runner import factory_loop, schedule_pipeline_for_topic
from article_factory.routes.admin import require_api_key
from article_factory.schemas import RunSummary
from article_factory.services.assignment_desk import (
    get_standing_order,
    standing_order_payload,
    suggest_topics_for_desk,
    upsert_standing_order,
)
from article_factory.services.flow_storage import read_flow
from article_factory.services.persona_selection import load_reporter_pool
from article_factory.services.puller_selection import idle_pullers_for_model
from article_factory.services.runtime_settings import load_runtime_settings
from article_factory.services.shift_windows import SHIFT_ORDER

router = APIRouter(prefix="/api/desks", dependencies=[Depends(require_api_key)])


class GenerateDeskTopicsBody(BaseModel):
    desk_path: str = Field(..., min_length=1)
    shift_key: str = "morning"
    count: int = Field(default=3, ge=1, le=20)


class SaveDeskTopicsBody(BaseModel):
    desk_path: str = Field(..., min_length=1)
    shift_key: str = Field(..., min_length=1)
    topics: list[str] = Field(default_factory=list)
    merge: bool = False


class DeskTestRunBody(BaseModel):
    desk_path: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    topic_slug: str | None = None
    reporter_persona_slug: str | None = None


async def _resolve_puller_for_model(db: Session, model: str) -> tuple[ControlPlaneClient, str]:
    runtime = load_runtime_settings(db)
    cp_url = (runtime.control_plane_url or "").strip()
    if not cp_url:
        raise ValueError("Control plane URL is not configured")
    cp = ControlPlaneClient(base_url=cp_url)
    pullers = await cp.list_pullers(active_only=False)
    idle = idle_pullers_for_model(pullers, model)
    if not idle:
        raise ValueError(f"No idle puller available for model {model}")
    puller_name = str(idle[0].get("puller_name") or "").strip()
    if not puller_name:
        raise ValueError("Could not resolve puller for model")
    return cp, puller_name


@router.post("/generate-topics")
async def post_generate_desk_topics(body: GenerateDeskTopicsBody, db: Session = Depends(get_db)) -> dict:
    shift_key = body.shift_key.strip().lower()
    if shift_key not in SHIFT_ORDER:
        raise HTTPException(status_code=400, detail="Invalid shift key")

    runtime = load_runtime_settings(db)
    model = (runtime.default_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="No default model configured — set one in Settings")

    try:
        cp, puller = await _resolve_puller_for_model(db, model)
        topics = await suggest_topics_for_desk(
            db,
            desk_path=body.desk_path,
            shift_key=shift_key,
            count=body.count,
            cp=cp,
            puller=puller,
            model=model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result: dict = {
        "desk_path": body.desk_path.strip(),
        "shift_key": shift_key,
        "topics": topics,
    }
    if not topics:
        result["warning"] = (
            "The model finished but returned no parseable topics. Try again or switch models."
        )
    return result


@router.post("/save-topics")
def post_save_desk_topics(body: SaveDeskTopicsBody, db: Session = Depends(get_db)) -> dict:
    shift_key = body.shift_key.strip().lower()
    if shift_key not in SHIFT_ORDER:
        raise HTTPException(status_code=400, detail="Invalid shift key")

    desk_path = body.desk_path.strip()
    cleaned_topics = [line.strip() for line in body.topics if line.strip()]
    if body.merge:
        existing = get_standing_order(db, desk_path=desk_path, shift_key=shift_key)
        prior = [line.strip() for line in (existing.topics if existing else []) if line.strip()]
        seen = set(prior)
        merged = list(prior)
        for topic in cleaned_topics:
            if topic not in seen:
                seen.add(topic)
                merged.append(topic)
        cleaned_topics = merged

    order = upsert_standing_order(
        db,
        desk_path=desk_path,
        shift_key=shift_key,
        topics=cleaned_topics,
        target_count=None,
    )
    db.commit()
    return {"order": standing_order_payload(order)}


@router.post("/test-run")
async def post_desk_test_run(body: DeskTestRunBody, db: Session = Depends(get_db)) -> dict:
    desk_path = body.desk_path.strip()
    try:
        flow = read_flow(desk_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Desk not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    topic_slug = (body.topic_slug or flow.edition_topic_slug or "general").strip() or "general"
    reporter = (body.reporter_persona_slug or "").strip() or None
    if not reporter:
        pool = load_reporter_pool(db, desk_path=desk_path, flow_version_id=None)
        reporter = pool[0] if pool else None

    runtime = load_runtime_settings(db)
    model = (runtime.default_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="No default model configured — set one in Settings")

    try:
        _cp, puller = await _resolve_puller_for_model(db, model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await factory_loop.ensure_running()
    run = await schedule_pipeline_for_topic(
        db,
        topic_slug=topic_slug,
        topic_prompt=body.prompt.strip(),
        selected_puller=puller,
        flow_path=desk_path,
        reporter_persona_slug=reporter,
    )
    return {"run": RunSummary.model_validate(run).model_dump()}
