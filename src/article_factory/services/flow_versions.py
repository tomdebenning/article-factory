from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from article_factory.models import FlowVersion
from article_factory.services.flow_schema import FlowDefinition, flow_from_dict, flow_to_dict, strip_runtime_overrides
from article_factory.services.flow_storage import read_flow, write_flow


def flow_content_hash(flow_dict: dict[str, Any]) -> str:
    payload = json.dumps(flow_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _next_version_number(db: Session, flow_path: str) -> int:
    latest = (
        db.query(FlowVersion)
        .filter_by(flow_path=flow_path)
        .order_by(FlowVersion.version_number.desc())
        .first()
    )
    return (latest.version_number + 1) if latest else 1


def create_flow_version(
    db: Session,
    flow_path: str,
    *,
    message: str = "",
    flow: FlowDefinition | None = None,
) -> FlowVersion:
    flow_obj = flow or read_flow(flow_path)
    cleaned = strip_runtime_overrides(flow_obj)
    content = flow_to_dict(cleaned)
    digest = flow_content_hash(content)
    existing = (
        db.query(FlowVersion)
        .filter_by(flow_path=flow_path, content_hash=digest)
        .order_by(FlowVersion.version_number.desc())
        .first()
    )
    if existing:
        return existing

    row = FlowVersion(
        flow_path=flow_path,
        version_number=_next_version_number(db, flow_path),
        content_hash=digest,
        flow_content=content,
        message=message.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def peek_next_version_number(db: Session, flow_path: str) -> int:
    return _next_version_number(db, flow_path)


def create_improved_flow_version(
    db: Session,
    flow_path: str,
    *,
    flow: FlowDefinition,
    source_version_number: int,
    message: str,
) -> FlowVersion:
    """Create a new numbered version from prompt improvement (never dedupes by hash)."""
    cleaned = strip_runtime_overrides(flow)
    content = flow_to_dict(cleaned)
    digest = flow_content_hash(content)
    version_number = _next_version_number(db, flow_path)
    row = FlowVersion(
        flow_path=flow_path,
        version_number=version_number,
        content_hash=digest,
        flow_content=content,
        message=message.strip() or f"v{version_number}-improved-from-v{source_version_number}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_flow_version_for_run(db: Session, flow_path: str) -> FlowVersion:
    latest = get_latest_flow_version(db, flow_path)
    if latest:
        return latest
    return create_flow_version(db, flow_path, message="Auto-created on first run")


def get_latest_flow_version(db: Session, flow_path: str) -> FlowVersion | None:
    return (
        db.query(FlowVersion)
        .filter_by(flow_path=flow_path)
        .order_by(FlowVersion.version_number.desc())
        .first()
    )


def list_flow_versions(db: Session, flow_path: str) -> list[FlowVersion]:
    return (
        db.query(FlowVersion)
        .filter_by(flow_path=flow_path)
        .order_by(FlowVersion.version_number.desc())
        .all()
    )


def get_flow_version(db: Session, version_id: int) -> FlowVersion | None:
    return db.get(FlowVersion, version_id)


def version_to_dict(row: FlowVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "flow_path": row.flow_path,
        "version_number": row.version_number,
        "content_hash": row.content_hash,
        "message": row.message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "display_name": (row.flow_content or {}).get("display_name"),
        "step_count": len((row.flow_content or {}).get("steps") or []),
    }


def diff_flow_versions(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    prev_steps = {step["step_key"]: step for step in previous.get("steps") or []}
    curr_steps = {step["step_key"]: step for step in current.get("steps") or []}
    changes: list[dict[str, Any]] = []
    for key in sorted(set(prev_steps) | set(curr_steps)):
        before = prev_steps.get(key)
        after = curr_steps.get(key)
        if before is None:
            changes.append({"step_key": key, "change": "added", "label": after.get("label") if after else key})
            continue
        if after is None:
            changes.append({"step_key": key, "change": "removed", "label": before.get("label")})
            continue
        for field in ("system_prompt", "user_prompt_template", "label"):
            if (before.get(field) or "") != (after.get(field) or ""):
                changes.append(
                    {
                        "step_key": key,
                        "change": "modified",
                        "field": field,
                        "label": after.get("label") or key,
                    }
                )
    return changes


def load_version_flow(row: FlowVersion) -> FlowDefinition:
    return flow_from_dict(dict(row.flow_content or {}))


def resolve_flow_for_run(db: Session, run) -> FlowDefinition:
    """Load the flow definition a run should execute (version snapshot when set)."""
    flow_path = (getattr(run, "flow_path", None) or "").strip()
    flow_version_id = getattr(run, "flow_version_id", None)
    if flow_version_id:
        version = get_flow_version(db, int(flow_version_id))
        if version is not None and version.flow_path == flow_path and version.flow_content:
            return load_version_flow(version)
    if not flow_path:
        raise FileNotFoundError("Run has no flow path")
    return read_flow(flow_path)


def resolve_flow_version_for_run(
    db: Session,
    flow_path: str,
    *,
    flow_version_id: int | None = None,
) -> FlowVersion:
    cleaned = flow_path.strip()
    if flow_version_id is not None:
        version = get_flow_version(db, flow_version_id)
        if version is not None and version.flow_path == cleaned:
            return version
    latest = get_latest_flow_version(db, cleaned)
    if latest:
        return latest
    return create_flow_version(db, cleaned, message="Auto-created on first run")


def apply_version_to_disk(db: Session, version_id: int) -> FlowVersion:
    version = get_flow_version(db, version_id)
    if version is None:
        raise ValueError("Flow version not found")
    flow = load_version_flow(version)
    write_flow(version.flow_path, flow)
    return version
