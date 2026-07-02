from __future__ import annotations

from typing import Any

from article_factory.models import FactoryRun, TopicQueueItem
from article_factory.services.factory_readiness import assess_factory_readiness
from article_factory.services.runtime_settings import RuntimeSettings

RETRY_SKIP_CHECK_IDS = frozenset({"topics"})


def is_queue_item_rerunnable(item: TopicQueueItem, run: FactoryRun | None) -> bool:
    """True when a finished queue item can be sent through the flow again."""
    if item.status == "queued":
        return False
    if run is not None and run.status == "running":
        return False
    if item.status == "running":
        return run is None or run.status != "running"
    if item.status in ("completed", "failed"):
        return True
    if run is not None and run.status in ("completed", "failed", "cancelled"):
        return True
    return False


def retry_blockers_from_readiness(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        check
        for check in readiness.get("checks", [])
        if check.get("id") not in RETRY_SKIP_CHECK_IDS and not check.get("ok")
    ]


async def assess_queue_item_retry(
    *,
    runtime: RuntimeSettings,
    loop_running: bool,
    active_run: FactoryRun | None,
    queue_counts: dict[str, int],
) -> dict[str, Any]:
    readiness = await assess_factory_readiness(
        runtime=runtime,
        loop_running=loop_running,
        active_run=active_run,
        queue_counts=queue_counts,
    )
    blockers = retry_blockers_from_readiness(readiness)
    can_retry = len(blockers) == 0

    if can_retry:
        if active_run is not None:
            message = "Ready — this prompt will run again after the current article finishes."
        else:
            message = "Ready — the factory will start this prompt shortly."
    else:
        message = "Fix the items below before re-running this prompt."

    return {
        "can_retry": can_retry,
        "message": message,
        "blockers": blockers,
    }
