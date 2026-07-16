from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.cms_client import CmsClient, CmsRequestError, best_effort_showroom
from article_factory.models import FactoryRun, RunTelemetry
from article_factory.services.batch_comparison import build_batch_comparison
from article_factory.services.runtime_settings import load_runtime_settings
from article_factory.services.telemetry_csv import _row_dict, csv_headers, telemetry_export_filename

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def batch_is_complete(db: Session, topic_queue_snapshot_id: int) -> bool:
    runs = (
        db.query(FactoryRun)
        .filter_by(topic_queue_snapshot_id=topic_queue_snapshot_id)
        .all()
    )
    if not runs:
        return False
    return all(run.status in _TERMINAL_STATUSES for run in runs)


def build_flow_batch_payload(db: Session, topic_queue_snapshot_id: int) -> dict[str, Any]:
    runs = (
        db.query(FactoryRun)
        .filter_by(topic_queue_snapshot_id=topic_queue_snapshot_id)
        .order_by(FactoryRun.started_at.asc())
        .all()
    )
    if not runs:
        raise CmsRequestError(f"No runs found for snapshot {topic_queue_snapshot_id}")

    flow_version_id = runs[0].flow_version_id
    selected_model = runs[0].selected_model or None
    comparison = build_batch_comparison(
        db,
        topic_queue_snapshot_id=topic_queue_snapshot_id,
        flow_version_id=flow_version_id,
        selected_model=selected_model,
    )

    telemetry_rows = (
        db.query(RunTelemetry)
        .filter_by(topic_queue_snapshot_id=topic_queue_snapshot_id)
        .order_by(RunTelemetry.started_at.asc())
        .all()
    )
    spreadsheet_headers = csv_headers()
    spreadsheet_rows = [_row_dict(db, row) for row in telemetry_rows]
    flow_path = comparison.get("flow_path") or runs[0].flow_path
    version_id = flow_version_id or 0

    return {
        "topic_queue_snapshot_id": topic_queue_snapshot_id,
        "flow_path": flow_path,
        "flow_version_id": flow_version_id,
        "selected_model": selected_model,
        "selected_puller": runs[0].selected_puller or None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": comparison.get("snapshot"),
        "summary": comparison.get("summary") or {},
        "error_groups": comparison.get("error_groups") or [],
        "turn_charts": comparison.get("turn_charts") or {},
        "topics": comparison.get("topics") or [],
        "runs": comparison.get("runs") or [],
        "spreadsheet": {
            "filename": telemetry_export_filename(flow_path, version_id),
            "headers": spreadsheet_headers,
            "rows": spreadsheet_rows,
        },
    }


async def publish_flow_batch_to_showroom(
    db: Session,
    *,
    topic_queue_snapshot_id: int,
    cms: CmsClient | None = None,
) -> dict[str, Any] | None:
    if not batch_is_complete(db, topic_queue_snapshot_id):
        return None

    runtime = load_runtime_settings(db)
    if cms is None:
        if not runtime.cms_url.strip() or not runtime.cms_api_key.strip():
            raise CmsRequestError("Showroom CMS is not configured")
        cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key)

    payload = build_flow_batch_payload(db, topic_queue_snapshot_id)
    result = await cms.post_flow_batch_complete(payload)
    await best_effort_showroom(
        f"flow_batch_published for snapshot {topic_queue_snapshot_id}",
        lambda: cms.post_run_event(
            {
                "run_id": f"snapshot-{topic_queue_snapshot_id}",
                "topic_slug": (payload.get("topics") or [{}])[0].get("topic_slug") or "general",
                "event": "flow_batch_published",
                "at": payload["completed_at"],
            }
        ),
    )
    logger.info(
        "Published flow batch snapshot %s (%s) to Showroom",
        topic_queue_snapshot_id,
        payload.get("flow_path"),
    )
    return result


async def maybe_publish_flow_batch_after_run(
    db: Session,
    run: FactoryRun,
    cms: CmsClient | None = None,
) -> None:
    db.refresh(run)
    if run.status not in _TERMINAL_STATUSES:
        return
    snapshot_id = run.topic_queue_snapshot_id
    if snapshot_id is None:
        return
    try:
        await publish_flow_batch_to_showroom(db, topic_queue_snapshot_id=snapshot_id, cms=cms)
    except Exception as exc:
        logger.warning(
            "Showroom flow batch publish failed for snapshot %s after %s: %s",
            snapshot_id,
            run.run_id,
            exc,
            exc_info=True,
        )
