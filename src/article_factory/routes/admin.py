from __future__ import annotations

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from article_factory.services.api_key_auth import require_configured_api_key
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.db import get_db
from article_factory.models import CompletedArticle, FactoryRun, FlowQueue, ShiftAssignment, StepExecution, TopicQueueItem
from article_factory.services.flow_switch import stop_all_runs, switch_active_flow
from article_factory.services.flow_paths import resolve_default_flow_path
from article_factory.services.flow_queues import ensure_default_flow_queue, list_flow_queues, resolve_queue_flow_path
from article_factory.services.factory_queue_depth import factory_queue_depth
from article_factory.services.flow_storage import ensure_default_flows
from article_factory.orchestrator.runner import factory_loop, run_pipeline_for_topic
from article_factory.services.showroom_publish import publish_article_to_showroom
from article_factory.services.showroom_status_sync import showroom_status_loop, sync_showroom_when_factory_busy
from article_factory.services.run_control import (
    is_run_cancelled,
    mark_run_cancelled_in_db,
    reconcile_stale_running_queue_items,
    request_run_cancel,
)
from article_factory.schemas import (
    ConnectionTestResult,
    CompletedArticleView,
    FactoryGatewayIdentityBody,
    FactorySettingsBody,
    FactorySettingsView,
    PullerView,
    QueueBatchBody,
    QueueItemBody,
    QueueRetryResult,
    RunSummary,
    StepExecutionView,
    StopAllRunsBody,
    SwitchFlowBody,
)
from article_factory.services.queue_retry import assess_queue_item_retry, is_queue_item_rerunnable
from article_factory.services.runtime_settings import (
    get_effective_factory_api_key,
    get_or_create_factory_settings,
    load_runtime_settings,
    normalize_base_url,
    update_factory_settings,
)
from article_factory.services.cms_connection import check_cms_connection
from article_factory.services.brave_search import brave_web_search, format_brave_results
from article_factory.services.active_board import build_active_overview
from article_factory.services.factory_identity import load_factory_identity, save_factory_display_name
from article_factory.services.factory_readiness import assess_factory_readiness
from article_factory.services.onboarding import morning_shift_onboarding
from article_factory.services.factory_stats import build_factory_stats
from article_factory.services.run_outputs import list_run_step_files, read_run_step_file
from article_factory.services.article_text import article_has_content
from article_factory.services.run_attachments import (
    list_run_workspace_attachment_summaries,
    read_run_workspace_file,
)
from article_factory.services.showroom_flow_publish import publish_flow_batch_to_showroom
from article_factory.services.step_trace import (
    manifest_step_tools_backfilled,
    merge_tools_into_manifest,
    step_executions_payload,
)
from article_factory.services.token_usage import enrich_manifest

router = APIRouter(prefix="/api")


def _resolve_queue_flow_path(db: Session, flow_path: str, flow_queue_id: int | None = None) -> str:
    if flow_queue_id is not None:
        queue = db.get(FlowQueue, flow_queue_id)
        if queue is not None:
            return resolve_queue_flow_path(db, queue)
    cleaned = (flow_path or "").strip()
    if cleaned:
        return cleaned
    return resolve_default_flow_path(db)


def _latest_run_for_item(db: Session, item_id: int) -> FactoryRun | None:
    return (
        db.query(FactoryRun)
        .filter_by(queue_item_id=item_id)
        .order_by(FactoryRun.started_at.desc())
        .first()
    )


def _queue_item_payload(db: Session, item: TopicQueueItem) -> dict:
    run = _latest_run_for_item(db, item.id)
    queue = db.get(FlowQueue, item.flow_queue_id) if item.flow_queue_id else None
    payload = {
        "id": item.id,
        "flow_queue_id": item.flow_queue_id,
        "flow_queue_name": queue.name if queue else None,
        "topic_slug": item.topic_slug,
        "flow_path": item.flow_path,
        "prompt": item.prompt,
        "status": item.status,
        "priority": item.priority,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "run_id": run.run_id if run else None,
        "run_status": run.status if run else None,
        "run_error": run.error if run else None,
        "current_step": run.current_step if run else None,
    }
    if run:
        payload["steps"] = step_executions_payload(db, run.run_id)
    else:
        payload["steps"] = []
    payload["rerunnable"] = is_queue_item_rerunnable(item, run)
    return payload


async def _retry_assessment(db: Session) -> dict:
    active = (
        db.query(FactoryRun)
        .filter(FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.desc())
        .first()
    )
    queued = db.query(TopicQueueItem).filter_by(status="queued").count()
    running = db.query(TopicQueueItem).filter_by(status="running").count()
    completed = db.query(TopicQueueItem).filter_by(status="completed").count()
    failed = db.query(TopicQueueItem).filter_by(status="failed").count()
    runtime = load_runtime_settings(db)
    return await assess_queue_item_retry(
        runtime=runtime,
        loop_running=factory_loop._running,
        active_run=active,
        queue_counts={
            "queued": queued,
            "running": running,
            "completed": completed,
            "failed": failed,
        },
    )


def require_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None, alias="api_key"),
    factory_api_key: str | None = Cookie(default=None, alias="factory_api_key"),
) -> None:
    require_configured_api_key(
        x_api_key=x_api_key,
        api_key=api_key,
        factory_api_key=factory_api_key,
    )


def require_api_key_header_or_query(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None, alias="api_key"),
    factory_api_key: str | None = Cookie(default=None, alias="factory_api_key"),
) -> None:
    """Allow API key via header, query string, or cookie for browser file downloads."""
    require_configured_api_key(
        x_api_key=x_api_key,
        api_key=api_key,
        factory_api_key=factory_api_key,
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _settings_view(db: Session) -> FactorySettingsView:
    row = get_or_create_factory_settings(db)
    runtime = load_runtime_settings(db)
    identity = load_factory_identity(db)
    return FactorySettingsView(
        control_plane_url=runtime.control_plane_url,
        cms_url=runtime.cms_url,
        cms_api_key=runtime.cms_api_key,
        default_puller=runtime.default_puller,
        default_model=runtime.default_model,
        default_flow_path=runtime.default_flow_path,
        brave_search_api_key=runtime.brave_search_api_key,
        brave_search_configured=bool(runtime.brave_search_api_key.strip()),
        gateway_id=identity.gateway_id,
        gateway_display_name=identity.gateway_display_name,
        display_timezone=runtime.display_timezone,
        auto_scheduler_enabled=runtime.auto_scheduler_enabled,
        updated_at=row.updated_at,
    )


@router.get("/settings", dependencies=[Depends(require_api_key)])
def get_settings(db: Session = Depends(get_db)) -> FactorySettingsView:
    return _settings_view(db)


@router.put("/settings", dependencies=[Depends(require_api_key)])
def put_settings(body: FactorySettingsBody, db: Session = Depends(get_db)) -> FactorySettingsView:
    update_factory_settings(
        db,
        {
            "control_plane_url": normalize_base_url(body.control_plane_url),
            "cms_url": normalize_base_url(body.cms_url),
            "cms_api_key": body.cms_api_key,
            "default_puller": body.default_puller,
            "default_model": body.default_model,
            "default_flow_path": body.default_flow_path.strip(),
            "brave_search_api_key": body.brave_search_api_key.strip(),
            "display_timezone": body.display_timezone.strip() or "UTC",
            "auto_scheduler_enabled": body.auto_scheduler_enabled,
        },
    )
    return _settings_view(db)


@router.put("/settings/gateway-identity", dependencies=[Depends(require_api_key)])
def put_gateway_identity(body: FactoryGatewayIdentityBody, db: Session = Depends(get_db)) -> FactorySettingsView:
    try:
        save_factory_display_name(db, body.gateway_display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _settings_view(db)


@router.post("/settings/test/control-plane", dependencies=[Depends(require_api_key)])
async def test_control_plane(
    body: FactorySettingsBody | None = None,
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    if body is not None:
        cp_url = normalize_base_url(body.control_plane_url)
    else:
        cp_url = load_runtime_settings(db).control_plane_url
    url = f"{cp_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
        return ConnectionTestResult(ok=True, message=f"Connected to control plane at {cp_url}")
    except Exception as exc:
        return ConnectionTestResult(ok=False, message=f"Control plane unreachable: {exc}")


@router.post("/settings/test/cms", dependencies=[Depends(require_api_key)])
async def test_cms(
    body: FactorySettingsBody | None = None,
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    if body is not None:
        cms_url = normalize_base_url(body.cms_url)
        cms_api_key = body.cms_api_key
    else:
        runtime = load_runtime_settings(db)
        cms_url = runtime.cms_url
        cms_api_key = runtime.cms_api_key
    ok, message = await check_cms_connection(cms_url, cms_api_key)
    return ConnectionTestResult(ok=ok, message=message)


@router.post("/settings/test/brave-search", dependencies=[Depends(require_api_key)])
async def test_brave_search(
    body: FactorySettingsBody | None = None,
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    if body is not None and body.brave_search_api_key.strip():
        api_key = body.brave_search_api_key.strip()
    else:
        api_key = load_runtime_settings(db).brave_search_api_key.strip()
    if not api_key:
        return ConnectionTestResult(
            ok=False,
            message="Enter a Brave Search API key before testing.",
        )
    try:
        payload = await brave_web_search(api_key=api_key, query="article factory test", count=1)
        preview = format_brave_results(payload).split("\n\n")[0]
        return ConnectionTestResult(ok=True, message=f"Brave Search connected. Sample: {preview[:180]}")
    except Exception as exc:
        return ConnectionTestResult(ok=False, message=f"Brave Search failed: {exc}")


@router.get("/control-plane/pullers", dependencies=[Depends(require_api_key)])
async def list_control_plane_pullers(db: Session = Depends(get_db)) -> dict:
    runtime = load_runtime_settings(db)
    cp = ControlPlaneClient(base_url=runtime.control_plane_url)
    try:
        pullers = await cp.list_pullers(active_only=False)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach control plane: {exc}") from exc
    return {
        "pullers": [
            PullerView(
                puller_name=p["puller_name"],
                status=p.get("status") or "unknown",
                supported_models=list(p.get("supported_models") or []),
                is_active=bool(p.get("is_active")),
                is_stale=bool(p.get("is_stale")),
                last_heartbeat_at=p.get("last_heartbeat_at"),
                current_task=p.get("current_task") if isinstance(p.get("current_task"), dict) else None,
            ).model_dump()
            for p in pullers
        ]
    }


@router.get("/control-plane/tasks/status", dependencies=[Depends(require_api_key)])
async def control_plane_task_status(
    conversation_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> dict:
    runtime = load_runtime_settings(db)
    cp = ControlPlaneClient(base_url=runtime.control_plane_url)
    try:
        status = await cp.get_task_status(conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach control plane: {exc}") from exc
    if status is None:
        return {"found": False, "conversation_id": conversation_id}
    return {"found": True, **status}


@router.get("/active/overview", dependencies=[Depends(require_api_key)])
def active_overview(db: Session = Depends(get_db), history_limit: int = 250) -> dict:
    return build_active_overview(db, history_limit=history_limit)


@router.get("/factory/status", dependencies=[Depends(require_api_key)])
async def factory_status(db: Session = Depends(get_db)) -> dict:
    await factory_loop.ensure_running()
    await showroom_status_loop.ensure_running()
    loop_alive = (
        factory_loop._running
        and factory_loop._task is not None
        and not factory_loop._task.done()
    )
    active_runs = (
        db.query(FactoryRun)
        .filter(FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.desc())
        .all()
    )
    active = active_runs[0] if active_runs else None

    def _run_summary(run: FactoryRun) -> dict:
        active_prompt: str | None = (run.topic_prompt or "").strip() or None
        if run.queue_item_id:
            item = db.get(TopicQueueItem, run.queue_item_id)
            if item:
                active_prompt = item.prompt
        if not active_prompt and run.shift_assignment_id:
            assignment = db.get(ShiftAssignment, run.shift_assignment_id)
            if assignment and assignment.prompt.strip():
                active_prompt = assignment.prompt
        from article_factory.services.flow_steps import flow_steps_payload_for_run

        summary = RunSummary.model_validate(run).model_dump()
        summary["topic_prompt"] = active_prompt
        summary["flow_steps"] = flow_steps_payload_for_run(db, run)
        summary["steps"] = step_executions_payload(db, run.run_id)
        return summary

    queued = db.query(TopicQueueItem).filter_by(status="queued").count()
    running = db.query(TopicQueueItem).filter_by(status="running").count()
    completed = db.query(TopicQueueItem).filter_by(status="completed").count()
    failed = db.query(TopicQueueItem).filter_by(status="failed").count()
    queue_counts = {
        "queued": queued,
        "running": running,
        "completed": completed,
        "failed": failed,
    }

    runtime = load_runtime_settings(db)
    state = "processing" if active_runs else "idle"

    readiness = await assess_factory_readiness(
        runtime=runtime,
        loop_running=loop_alive,
        active_run=active,
        queue_counts=queue_counts,
        active_run_count=len(active_runs),
    )

    payload: dict = {
        "loop_running": loop_alive,
        "state": state,
        "default_model": runtime.default_model,
        "default_puller": runtime.default_puller,
        "control_plane_url": runtime.control_plane_url,
        "queue_depth": factory_queue_depth(db),
        "queue_counts": queue_counts,
        "readiness": readiness,
        "onboarding": morning_shift_onboarding(db, setup_complete=readiness["setup_complete"]),
        "active_run": None,
        "active_runs": [_run_summary(run) for run in active_runs],
        "flow_queues": list_flow_queues(db),
    }
    if active:
        payload["active_run"] = _run_summary(active)
    await sync_showroom_when_factory_busy(active_run_count=len(active_runs))
    return payload


@router.post("/factory/stop-all-runs", dependencies=[Depends(require_api_key)])
async def factory_stop_all_runs(body: StopAllRunsBody, db: Session = Depends(get_db)) -> dict:
    try:
        return await stop_all_runs(
            db,
            requeue=body.requeue,
            flow_path=body.flow_path.strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/factory/switch-flow", dependencies=[Depends(require_api_key)])
async def factory_switch_flow(body: SwitchFlowBody, db: Session = Depends(get_db)) -> dict:
    try:
        result = await switch_active_flow(
            db,
            flow_path=body.flow_path,
            set_as_default=body.set_as_default,
            clear_history=body.clear_history,
            update_queued=body.update_queued,
            requeue_running=body.requeue_running,
            start_prompt=body.start_prompt.strip() or None,
            topic_slug=body.topic_slug,
        )
        if result.get("queued_item_id") is not None:
            factory_loop.request_dispatch()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Flow not found: {body.flow_path}") from exc


@router.get("/queue", dependencies=[Depends(require_api_key)])
def list_queue(db: Session = Depends(get_db)) -> dict:
    items = db.query(TopicQueueItem).order_by(TopicQueueItem.created_at.desc()).limit(100).all()
    return {"items": [_queue_item_payload(db, item) for item in items]}


@router.get("/queue/{item_id}/retry-status", dependencies=[Depends(require_api_key)])
async def queue_item_retry_status(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(TopicQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Queue item not found")
    assessment = await _retry_assessment(db)
    run = _latest_run_for_item(db, item.id)
    return {
        **assessment,
        "item_status": item.status,
        "run_error": run.error if run else None,
        "retriable": is_queue_item_rerunnable(item, run),
    }


@router.post("/queue/{item_id}/retry", dependencies=[Depends(require_api_key)])
async def retry_queue_item(item_id: int, db: Session = Depends(get_db)) -> QueueRetryResult:
    item = db.get(TopicQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Queue item not found")

    run = _latest_run_for_item(db, item.id)
    if not is_queue_item_rerunnable(item, run):
        detail = item.status
        if run is not None:
            detail = f"{item.status} (run: {run.status})"
        return QueueRetryResult(
            ok=False,
            message=f"This prompt cannot be re-run while its status is “{detail}”.",
            item=_queue_item_payload(db, item),
        )

    assessment = await _retry_assessment(db)
    if not assessment["can_retry"]:
        return QueueRetryResult(
            ok=False,
            message=assessment["message"],
            item=_queue_item_payload(db, item),
            blockers=assessment["blockers"],
        )

    queued_priorities = [
        row[0]
        for row in db.query(TopicQueueItem.priority)
        .filter_by(status="queued")
        .order_by(TopicQueueItem.priority)
        .all()
    ]
    item.status = "queued"
    item.priority = (queued_priorities[0] - 1) if queued_priorities else 0
    db.commit()
    db.refresh(item)

    active = (
        db.query(FactoryRun)
        .filter(FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.desc())
        .first()
    )
    if active:
        message = "Prompt queued for re-run — it will start after the current article finishes."
    else:
        message = "Prompt queued for re-run — the factory will start it shortly."
        factory_loop.request_dispatch()

    return QueueRetryResult(
        ok=True,
        message=message,
        item=_queue_item_payload(db, item),
    )


@router.post("/queue", dependencies=[Depends(require_api_key)])
def enqueue(body: QueueItemBody, db: Session = Depends(get_db)) -> dict:
    default_queue = ensure_default_flow_queue(db)
    flow_queue_id = body.flow_queue_id or default_queue.id
    queue = db.get(FlowQueue, flow_queue_id)
    topic_slug = body.topic_slug
    if queue is not None and topic_slug == "general" and queue.topic_slug:
        topic_slug = queue.topic_slug
    item = TopicQueueItem(
        flow_queue_id=flow_queue_id,
        topic_slug=topic_slug,
        flow_path=_resolve_queue_flow_path(db, body.flow_path, flow_queue_id),
        prompt=body.prompt,
        priority=body.priority,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    factory_loop.request_dispatch()
    return {"id": item.id, "status": item.status}


@router.post("/queue/batch", dependencies=[Depends(require_api_key)])
def enqueue_batch(body: QueueBatchBody, db: Session = Depends(get_db)) -> dict:
    default_queue = ensure_default_flow_queue(db)
    flow_queue_id = body.flow_queue_id or default_queue.id
    queue = db.get(FlowQueue, flow_queue_id)
    topic_slug = body.topic_slug
    if queue is not None and topic_slug == "general" and queue.topic_slug:
        topic_slug = queue.topic_slug
    resolved_flow = _resolve_queue_flow_path(db, body.flow_path, flow_queue_id)
    created: list[dict] = []
    for index, line in enumerate(body.topics):
        prompt = line.strip()
        if not prompt:
            continue
        item = TopicQueueItem(
            flow_queue_id=flow_queue_id,
            topic_slug=topic_slug,
            flow_path=resolved_flow,
            prompt=prompt,
            priority=body.priority + index,
        )
        db.add(item)
        db.flush()
        created.append({"id": item.id, "prompt": item.prompt, "status": item.status})
    db.commit()
    if created:
        factory_loop.request_dispatch()
    return {"count": len(created), "items": created}


def _completed_article_payload(db: Session, article: CompletedArticle) -> dict:
    run = db.query(FactoryRun).filter_by(run_id=article.run_id).one_or_none()
    stored_manifest = article.manifest or (run.manifest if run else None) or {}
    base_manifest = stored_manifest
    if run is not None:
        base_manifest = merge_tools_into_manifest(
            stored_manifest,
            step_executions_payload(db, run.run_id),
        )
    manifest = enrich_manifest(
        base_manifest,
        selected_model=run.selected_model if run else "",
        body_markdown=article.body_markdown,
    )
    if run is not None and manifest_step_tools_backfilled(stored_manifest, base_manifest):
        article.manifest = manifest
        run.manifest = manifest
        db.commit()
    payload = CompletedArticleView.model_validate(article).model_dump()
    payload["manifest"] = manifest
    payload["model"] = str(manifest.get("selected_model") or "").strip() or "—"
    payload["stats"] = manifest.get("stats") or {}
    payload["has_content"] = article_has_content(article.body_markdown)
    payload["step_files"] = list_run_step_files(article.run_id)
    payload["workspace_files"] = list_run_workspace_attachment_summaries(article.run_id)
    payload["run_exists"] = run is not None
    payload["run_status"] = run.status if run is not None else None
    return payload


@router.get("/stats", dependencies=[Depends(require_api_key)])
def get_factory_stats(db: Session = Depends(get_db), recent_limit: int = 50) -> dict:
    return build_factory_stats(db, recent_limit=recent_limit)


@router.get("/articles", dependencies=[Depends(require_api_key)])
def list_articles(db: Session = Depends(get_db)) -> dict:
    articles = (
        db.query(CompletedArticle)
        .order_by(CompletedArticle.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "articles": [_completed_article_payload(db, article) for article in articles]
    }


@router.get("/articles/{run_id}", dependencies=[Depends(require_api_key)])
def get_article(run_id: str, db: Session = Depends(get_db)) -> dict:
    article = db.query(CompletedArticle).filter_by(run_id=run_id).one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"article": _completed_article_payload(db, article)}


@router.get("/articles/{run_id}/step-files/{filename}", dependencies=[Depends(require_api_key)])
def get_article_step_file(run_id: str, filename: str, db: Session = Depends(get_db)) -> dict:
    article = db.query(CompletedArticle).filter_by(run_id=run_id).one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    try:
        content = read_run_step_file(run_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Step file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, "filename": filename, "content": content}


@router.get("/articles/{run_id}/workspace-files/{file_path:path}", dependencies=[Depends(require_api_key)])
def get_article_workspace_file(run_id: str, file_path: str, db: Session = Depends(get_db)) -> dict:
    article = db.query(CompletedArticle).filter_by(run_id=run_id).one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    try:
        payload = read_run_workspace_file(run_id, file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workspace file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, **payload}


@router.post("/runs/{run_id}/recover-accept", dependencies=[Depends(require_api_key)])
async def recover_missed_accept_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    from article_factory.cms_client import CmsClient
    from article_factory.orchestrator.runner import _cms_configured, _complete_run
    from article_factory.services.run_recovery import build_recovery_from_missed_accept

    run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    recovery = build_recovery_from_missed_accept(db, run)
    if recovery is None:
        raise HTTPException(
            status_code=400,
            detail="Run is not eligible for missed-accept recovery",
        )

    draft, step_records = recovery
    runtime = load_runtime_settings(db)
    cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key) if _cms_configured(runtime) else None
    await _complete_run(db, run, draft, step_records, cms)
    db.refresh(run)
    return {"ok": True, "run": RunSummary.model_validate(run).model_dump()}


@router.post("/runs/{run_id}/publish", dependencies=[Depends(require_api_key)])
async def publish_run_to_showroom(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    article = db.query(CompletedArticle).filter_by(run_id=run_id).one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="No completed article for this run")

    try:
        result = await publish_article_to_showroom(db, run=run, article=article)
    except Exception as exc:
        run.error = str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if run.status == "failed":
        run.status = "completed"
    run.error = None
    if run.queue_item_id:
        item = db.get(TopicQueueItem, run.queue_item_id)
        if item and item.status == "failed":
            item.status = "completed"
    db.commit()
    return {"ok": True, "result": result, "run": RunSummary.model_validate(run).model_dump()}


@router.post("/flow-batches/{snapshot_id}/publish", dependencies=[Depends(require_api_key)])
async def publish_flow_batch_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        result = await publish_flow_batch_to_showroom(db, topic_queue_snapshot_id=snapshot_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=409, detail="Flow batch is not complete yet")
    return {"ok": True, "result": result}


@router.get("/runs", dependencies=[Depends(require_api_key)])
def list_runs(db: Session = Depends(get_db)) -> dict:
    runs = db.query(FactoryRun).order_by(FactoryRun.started_at.desc()).limit(50).all()
    payload = []
    for run in runs:
        summary = RunSummary.model_validate(run).model_dump()
        summary["steps"] = step_executions_payload(db, run.run_id)
        payload.append(summary)
    return {"runs": payload}


@router.get("/runs/{run_id}", dependencies=[Depends(require_api_key)])
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    from article_factory.services.flow_steps import flow_steps_payload_for_run

    run_summary = RunSummary.model_validate(run).model_dump()
    run_summary["flow_steps"] = flow_steps_payload_for_run(db, run)
    if run.flow_version_id:
        from article_factory.services.flow_versions import get_flow_version

        version = get_flow_version(db, run.flow_version_id)
        if version:
            run_summary["flow_version_number"] = version.version_number
            run_summary["flow_version_message"] = version.message
    if run.topic_queue_snapshot_id:
        from article_factory.services.flow_performance import _snapshot_label

        run_summary["topic_queue_label"] = _snapshot_label(db, run.topic_queue_snapshot_id)
    return {
        "run": run_summary,
        "steps": step_executions_payload(db, run_id),
        "step_files": list_run_step_files(run_id),
    }


@router.get("/runs/{run_id}/step-files/{filename}", dependencies=[Depends(require_api_key)])
def get_run_step_file(run_id: str, filename: str) -> dict:
    try:
        content = read_run_step_file(run_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Step file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id, "filename": filename, "content": content}


@router.post("/runs/{run_id}/stop", dependencies=[Depends(require_api_key)])
async def stop_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "running":
        return {
            "ok": False,
            "message": f"Run is not active (status: {run.status}).",
            "run": RunSummary.model_validate(run).model_dump(),
        }
    if await is_run_cancelled(run_id):
        return {
            "ok": False,
            "message": "Stop already requested.",
            "run": RunSummary.model_validate(run).model_dump(),
        }
    await request_run_cancel(run_id)
    queue_item_id = run.queue_item_id
    mark_run_cancelled_in_db(db, run)
    db.commit()
    cancelled_workers = factory_loop.cancel_run_workers(
        run_ids=[run_id],
        queue_item_ids=[queue_item_id] if queue_item_id is not None else [],
    )
    reconcile_stale_running_queue_items(db)
    db.commit()
    if cancelled_workers == 0:
        from article_factory.services.run_control import clear_run_cancel

        await clear_run_cancel(run_id)
    factory_loop.request_dispatch()
    db.refresh(run)
    return {
        "ok": True,
        "message": "Run stopped.",
        "run": RunSummary.model_validate(run).model_dump(),
    }


@router.delete("/runs/{run_id}", dependencies=[Depends(require_api_key)])
def delete_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.query(FactoryRun).filter_by(run_id=run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        raise HTTPException(
            status_code=409,
            detail="Stop the run before deleting it.",
        )

    queue_item_id = run.queue_item_id
    db.query(StepExecution).filter_by(run_id=run_id).delete()
    db.query(CompletedArticle).filter_by(run_id=run_id).delete()
    db.delete(run)
    if queue_item_id:
        item = db.get(TopicQueueItem, queue_item_id)
        if item and item.status in ("running", "failed", "completed"):
            item.status = "queued"
    db.commit()
    return {"ok": True, "deleted_run_id": run_id}


@router.post("/runs/trigger", dependencies=[Depends(require_api_key)])
async def trigger_run(body: QueueItemBody, db: Session = Depends(get_db)) -> dict:
    ensure_default_flows()
    run = await run_pipeline_for_topic(
        db,
        topic_slug=body.topic_slug,
        topic_prompt=body.prompt,
        flow_path=body.flow_path,
    )
    return RunSummary.model_validate(run).model_dump()
