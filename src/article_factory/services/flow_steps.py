from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from article_factory.models import FactoryRun
from article_factory.services.flow_paths import resolve_default_flow_path
from article_factory.services.flow_storage import read_flow

logger = logging.getLogger(__name__)

LEGACY_STEP_LABELS: dict[str, str] = {
    "writer": "Writer",
    "fact_asserter": "Fact asserter",
    "source_finder": "Source finder",
    "review": "Review",
}


def flow_steps_payload(flow_path: str) -> list[dict]:
    cleaned = (flow_path or "").strip()
    if not cleaned:
        return []
    try:
        flow = read_flow(cleaned)
    except Exception:
        logger.debug("Could not load flow steps for %s", cleaned, exc_info=True)
        return []

    return _steps_from_flow(flow)


def flow_steps_payload_for_run(db: Session, run: FactoryRun) -> list[dict]:
    from article_factory.services.flow_versions import resolve_flow_for_run

    try:
        flow = resolve_flow_for_run(db, run)
    except Exception:
        logger.debug("Could not load versioned flow steps for run %s", run.run_id, exc_info=True)
        return flow_steps_payload(run.flow_path or "")
    return _steps_from_flow(flow)


def _steps_from_flow(flow) -> list[dict]:
    return [
        {
            "step_key": step.step_key,
            "label": (step.label or "").strip() or step.step_key.replace("_", " "),
            "order": step.order,
        }
        for step in sorted(flow.steps, key=lambda item: item.order)
    ]


def flow_path_for_run(db: Session, run: FactoryRun | None) -> str:
    if run is not None:
        path = (run.flow_path or "").strip()
        if path:
            return path
    return resolve_default_flow_path(db)


def heartbeat_agents(db: Session, active_run: FactoryRun | None) -> list[dict[str, str]]:
    """Step agents to register on the control plane for the active or default flow."""
    flow_path = flow_path_for_run(db, active_run)
    steps = flow_steps_payload(flow_path)
    if steps:
        return [
            {
                "step_key": step["step_key"],
                "display_name": step["label"],
            }
            for step in steps
        ]

    return [
        {"step_key": key, "display_name": label}
        for key, label in LEGACY_STEP_LABELS.items()
    ]


def step_display_name(flow_path: str | None, step_key: str) -> str:
    for step in flow_steps_payload(flow_path or ""):
        if step["step_key"] == step_key:
            return step["label"]
    legacy = LEGACY_STEP_LABELS.get(step_key)
    if legacy:
        return legacy
    return step_key.replace("_", " ")
