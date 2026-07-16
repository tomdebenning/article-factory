from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from article_factory.config import settings
from article_factory.db import init_db
from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.orchestrator.runner import factory_loop
from article_factory.routes.admin import router as admin_router
from article_factory.routes.auth import router as auth_router
from article_factory.routes.flow_queues import router as flow_queues_router
from article_factory.routes.flows import router as flows_router
from article_factory.routes.flow_performance import router as flow_performance_router
from article_factory.routes.telemetry import router as telemetry_router
from article_factory.routes.prompt_improvement import router as prompt_improvement_router
from article_factory.routes.personas import router as personas_router
from article_factory.services.flow_queues import ensure_default_flow_queue
from article_factory.services.flow_storage import ensure_default_flows
from article_factory.services.queue_presets import migrate_file_presets_to_db
from article_factory.services.control_plane_heartbeat import control_plane_heartbeat_loop
from article_factory.services.factory_api_key_cache import warm_factory_api_key_cache
from article_factory.services.factory_readiness import assess_factory_readiness
from article_factory.services.showroom_status_sync import showroom_status_loop
from article_factory.services.prompt_improvement_runner import prompt_improvement_runner
from article_factory.services.runtime_settings import get_or_create_factory_settings, load_runtime_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from article_factory.db import SessionLocal

    db = SessionLocal()
    try:
        ensure_default_flows()
        get_or_create_factory_settings(db)
        ensure_default_flow_queue(db)
        migrated = migrate_file_presets_to_db(db)
        if migrated:
            logger.info("Imported %s legacy queue preset file(s) into saved_queues", migrated)
        db.commit()
        warm_factory_api_key_cache(db)
    finally:
        db.close()

    async def log_startup_readiness() -> None:
        db = SessionLocal()
        try:
            runtime = load_runtime_settings(db)
            active_run_count = db.query(FactoryRun).filter_by(status="running").count()
            active = (
                db.query(FactoryRun)
                .filter(FactoryRun.status == "running")
                .order_by(FactoryRun.started_at.desc())
                .first()
            )
            queue_counts = {
                "queued": db.query(TopicQueueItem).filter_by(status="queued").count(),
                "running": db.query(TopicQueueItem).filter_by(status="running").count(),
                "completed": db.query(TopicQueueItem).filter_by(status="completed").count(),
                "failed": db.query(TopicQueueItem).filter_by(status="failed").count(),
            }
            readiness = await assess_factory_readiness(
                runtime=runtime,
                loop_running=True,
                active_run=active,
                queue_counts=queue_counts,
                active_run_count=active_run_count,
            )
            issues = readiness.get("issue_checks") or []
            if issues:
                logger.warning(
                    "Factory configuration needs attention: %s",
                    "; ".join(f"{c['label']}: {c['message']}" for c in issues),
                )
            elif readiness.get("setup_complete"):
                logger.info("Factory configuration validated")
        except Exception:
            logger.exception("Startup readiness check failed")
        finally:
            db.close()

    await factory_loop.start()
    await control_plane_heartbeat_loop.start()
    await showroom_status_loop.start()
    await prompt_improvement_runner.start()
    asyncio.create_task(log_startup_readiness())

    async def push_showroom_if_busy() -> None:
        from article_factory.services.showroom_status_sync import refresh_showroom_status

        db = SessionLocal()
        try:
            if db.query(FactoryRun).filter_by(status="running").count() > 0:
                await refresh_showroom_status()
        finally:
            db.close()

    asyncio.create_task(push_showroom_if_busy())
    yield
    await showroom_status_loop.stop()
    await control_plane_heartbeat_loop.stop()
    await factory_loop.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Article Factory", version="0.1.0", lifespan=lifespan)
    cors_kwargs: dict = {
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if settings.cors_origin_list == ["*"]:
        cors_kwargs["allow_origins"] = ["*"]
    else:
        cors_kwargs["allow_origins"] = settings.cors_origin_list
        cors_kwargs["allow_credentials"] = True
    app.add_middleware(CORSMiddleware, **cors_kwargs)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(flow_queues_router)
    app.include_router(flows_router)
    app.include_router(flow_performance_router)
    app.include_router(telemetry_router)
    app.include_router(prompt_improvement_router)
    app.include_router(personas_router)
    return app
