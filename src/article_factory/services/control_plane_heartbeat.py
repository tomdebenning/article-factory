from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from sqlalchemy.orm import Session

from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import FactoryRun
from article_factory.services.flow_steps import heartbeat_agents, step_display_name
from article_factory.services.run_recovery import latest_step_execution
from article_factory.services.factory_identity import load_factory_identity
from article_factory.services.runtime_settings import load_runtime_settings
from article_factory.workers.executor import worker_agent_id

logger = logging.getLogger(__name__)

STEP_AGENT_LABELS: dict[str, str] = {
    "writer": "Article Factory — Writer",
    "fact_asserter": "Article Factory — Fact asserter",
    "source_finder": "Article Factory — Source finder",
    "review": "Article Factory — Review",
}


def _agent_display_name(active_run: FactoryRun | None, step_key: str, label: str) -> str:
    if label and label != step_key:
        return f"Article Factory — {label}"
    legacy = STEP_AGENT_LABELS.get(step_key)
    if legacy:
        return legacy
    flow_path = (active_run.flow_path or "").strip() if active_run else ""
    readable = step_display_name(flow_path or None, step_key)
    return f"Article Factory — {readable}"


async def send_control_plane_heartbeats(
    cp: ControlPlaneClient,
    *,
    db: Session,
    active_run: FactoryRun | None,
    gateway_id: str,
    gateway_display_name: str,
    extra_node_info: dict[str, Any] | None = None,
) -> None:
    """Register the factory as a control-plane gateway and heartbeat pipeline agents."""
    agents = heartbeat_agents(db, active_run)
    node_status = "busy" if active_run else "idle"
    node_info: dict[str, Any] = {
        "display_name": gateway_display_name,
        "kind": "article-factory",
    }
    if extra_node_info:
        node_info.update(extra_node_info)
    await cp.post_node_heartbeat(
        {
            "node_id": gateway_id,
            "status": node_status,
            "agent_count": len(agents),
            "running_agent_count": 1 if active_run else 0,
            "descriptive_info": node_info,
        }
    )

    active_step = active_run.current_step if active_run else None
    for agent in agents:
        step_key = agent["step_key"]
        label = agent["display_name"]
        if step_key == active_step:
            agent_status = "waiting_for_llm"
            info: dict[str, Any] = {
                "display_name": _agent_display_name(active_run, step_key, label),
                "gateway_id": gateway_id,
            }
            if active_run:
                info["run_id"] = active_run.run_id
                info["topic_slug"] = active_run.topic_slug
                if active_run.selected_puller:
                    info["puller"] = active_run.selected_puller
                if active_run.selected_model:
                    info["model"] = active_run.selected_model
        else:
            agent_status = "idle"
            info = {
                "display_name": _agent_display_name(active_run, step_key, label),
                "gateway_id": gateway_id,
            }
        await cp.post_agent_heartbeat(
            {
                "agent_id": worker_agent_id(step_key),
                "status": agent_status,
                "descriptive_info": info,
            }
        )


def effective_gateway_id() -> str:
    configured = settings.gateway_id.strip()
    if configured:
        return configured
    host = socket.gethostname().split(".")[0] or "local"
    return f"factory-{host}"


def _active_run(db: Session) -> FactoryRun | None:
    return (
        db.query(FactoryRun)
        .filter(FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.desc())
        .first()
    )


async def control_plane_heartbeat_tick(db: Session) -> None:
    runtime = load_runtime_settings(db)
    cp_url = runtime.control_plane_url.strip()
    if not cp_url:
        return

    active = _active_run(db)
    cp = ControlPlaneClient(base_url=cp_url)
    identity = load_factory_identity(db)
    extra_info: dict[str, Any] = {}
    if active:
        step = latest_step_execution(db, active.run_id)
        extra_info = {
            "active_run_id": active.run_id,
            "current_step": active.current_step,
            "active_puller": active.selected_puller or None,
            "active_model": active.selected_model or None,
        }
        if step:
            extra_info["step_status"] = step.status
            if step.puller:
                extra_info["step_puller"] = step.puller

    try:
        await send_control_plane_heartbeats(
            cp,
            db=db,
            active_run=active,
            gateway_id=identity.gateway_id,
            gateway_display_name=identity.gateway_display_name,
            extra_node_info=extra_info or None,
        )
    except Exception:
        logger.exception("Control plane heartbeat failed")


class ControlPlaneHeartbeatLoop:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._task:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        from article_factory.db import SessionLocal

        while self._running:
            db = SessionLocal()
            try:
                await control_plane_heartbeat_tick(db)
            except Exception:
                logger.exception("Control plane heartbeat loop error")
            finally:
                db.close()
            try:
                await asyncio.sleep(settings.heartbeat_interval_seconds)
            except asyncio.CancelledError:
                break


control_plane_heartbeat_loop = ControlPlaneHeartbeatLoop()
