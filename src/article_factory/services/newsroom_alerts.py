"""Operational alerts for the 24-hour newsroom scheduler."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from article_factory.models import NewsroomAlert


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_alert(
    db: Session,
    *,
    kind: str,
    message: str,
    severity: str = "warning",
    shift_plan_id: int | None = None,
    dedupe_key: str | None = None,
) -> NewsroomAlert:
    if dedupe_key:
        existing = (
            db.query(NewsroomAlert)
            .filter_by(dedupe_key=dedupe_key, resolved_at=None)
            .order_by(NewsroomAlert.created_at.desc())
            .first()
        )
        if existing is not None:
            existing.message = message
            existing.severity = severity
            db.flush()
            return existing

    row = NewsroomAlert(
        kind=kind.strip(),
        severity=severity.strip() or "warning",
        message=message.strip(),
        shift_plan_id=shift_plan_id,
        dedupe_key=(dedupe_key or "").strip() or None,
    )
    db.add(row)
    db.flush()
    return row


def list_active_alerts(db: Session, *, limit: int = 20) -> list[NewsroomAlert]:
    capped = max(1, min(limit, 50))
    return (
        db.query(NewsroomAlert)
        .filter(NewsroomAlert.resolved_at.is_(None))
        .order_by(NewsroomAlert.created_at.desc())
        .limit(capped)
        .all()
    )


def alert_payload(row: NewsroomAlert) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "severity": row.severity,
        "message": row.message,
        "shift_plan_id": row.shift_plan_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def alerts_payload(db: Session, *, limit: int = 20) -> list[dict]:
    return [alert_payload(row) for row in list_active_alerts(db, limit=limit)]


def resolve_alerts_for_plan(db: Session, *, shift_plan_id: int, kinds: set[str] | None = None) -> int:
    query = db.query(NewsroomAlert).filter_by(shift_plan_id=shift_plan_id, resolved_at=None)
    if kinds:
        query = query.filter(NewsroomAlert.kind.in_(sorted(kinds)))
    rows = query.all()
    now = _utc_now()
    for row in rows:
        row.resolved_at = now
    db.flush()
    return len(rows)
