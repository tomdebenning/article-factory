from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from article_factory.cms_client import CmsClient
from article_factory.config import settings
from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.orchestrator.pipeline import push_factory_status
from article_factory.services.runtime_settings import RuntimeSettings, load_runtime_settings

logger = logging.getLogger(__name__)

MAX_SHOWROOM_ACTIVE_RUNS = 3
_PUSH_DEBOUNCE_SECONDS = 4.0

_last_push_at = 0.0
_push_in_flight = False


def _cms_configured(runtime: RuntimeSettings) -> bool:
    return bool(runtime.cms_url.strip()) and bool(runtime.cms_api_key.strip())


def _active_runs(db: Session) -> list[FactoryRun]:
    return (
        db.query(FactoryRun)
        .filter(FactoryRun.status == "running")
        .order_by(FactoryRun.started_at.asc())
        .limit(MAX_SHOWROOM_ACTIVE_RUNS)
        .all()
    )


async def push_showroom_factory_status(db: Session, cms: CmsClient) -> None:
    running = _active_runs(db)
    queue_depth = db.query(TopicQueueItem).filter_by(status="queued").count()
    await push_factory_status(
        cms,
        db=db,
        state="running" if running else "idle",
        active_run=running[0] if running else None,
        active_runs=running,
        queue_depth=queue_depth,
        topic_slug=running[0].topic_slug if running else None,
    )


async def refresh_showroom_status(*, max_attempts: int = 4) -> bool:
    """Push factory status to Showroom using a fresh DB session with lock retries."""
    global _last_push_at, _push_in_flight

    if _push_in_flight:
        return False

    _push_in_flight = True
    try:
        from article_factory.db import SessionLocal

        for attempt in range(max_attempts):
            db = SessionLocal()
            try:
                runtime = load_runtime_settings(db)
                if not _cms_configured(runtime):
                    return False
                cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key)
                await push_showroom_factory_status(db, cms)
                _last_push_at = time.monotonic()
                return True
            except OperationalError as exc:
                db.rollback()
                message = str(getattr(exc, "orig", exc))
                if "database is locked" not in message.lower() or attempt >= max_attempts - 1:
                    logger.warning("Showroom status refresh failed (database)", exc_info=True)
                    return False
                time.sleep(0.05 * (2**attempt))
            except Exception:
                logger.warning("Showroom status refresh failed", exc_info=True)
                return False
            finally:
                db.close()
        return False
    finally:
        _push_in_flight = False


def schedule_showroom_status_refresh(*, force: bool = False) -> None:
    """Push status to Showroom immediately (debounced unless force=True)."""
    global _last_push_at

    now = time.monotonic()
    if not force and (now - _last_push_at) < _PUSH_DEBOUNCE_SECONDS:
        showroom_status_loop.request_refresh()
        return

    showroom_status_loop.request_refresh()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(refresh_showroom_status())


async def sync_showroom_when_factory_busy(*, active_run_count: int) -> None:
    """Called from /api/factory/status when the factory has active runs."""
    if active_run_count <= 0:
        return
    schedule_showroom_status_refresh(force=True)


async def showroom_status_tick(db: Session) -> None:
    """Push live factory status to Showroom CMS. Never touches the control plane."""
    runtime = load_runtime_settings(db)
    if not _cms_configured(runtime):
        return

    cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key)
    try:
        await push_showroom_factory_status(db, cms)
        global _last_push_at
        _last_push_at = time.monotonic()
    except OperationalError:
        logger.warning("Showroom factory status sync hit database lock — refreshing in new session")
        await refresh_showroom_status()
    except Exception:
        logger.exception("Showroom factory status sync failed")


class ShowroomStatusLoop:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._refresh_event: asyncio.Event | None = None

    @property
    def is_alive(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def request_refresh(self) -> None:
        if self._running and self._refresh_event is not None:
            self._refresh_event.set()

    async def ensure_running(self) -> None:
        if not self.is_alive:
            await self.start()

    async def start(self) -> None:
        if self.is_alive:
            return
        self._running = True
        self._refresh_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        logger.info("Showroom status sync loop started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._refresh_event = None

    async def _loop(self) -> None:
        while self._running:
            await refresh_showroom_status()
            event = self._refresh_event
            if event is None:
                break
            try:
                try:
                    await asyncio.wait_for(
                        event.wait(),
                        timeout=settings.heartbeat_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
                event.clear()
            except asyncio.CancelledError:
                break


showroom_status_loop = ShowroomStatusLoop()
