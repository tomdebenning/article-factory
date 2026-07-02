from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def run_worker(step_key: str) -> None:
    """Placeholder worker process — polls for step jobs (Redis/DB queue in a later iteration)."""
    logger.info("Worker %s started (stub — orchestrator runs steps in-process for v1)", step_key)
    while True:
        time.sleep(60)
