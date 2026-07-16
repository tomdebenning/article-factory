from __future__ import annotations

from sqlalchemy.orm import Session

from article_factory.models import RunErrorTag
from article_factory.services.run_recovery import commit_with_retry
from article_factory.services.run_error_classification import ERROR_GROUPS, error_group_label


def upsert_run_error_tag(
    db: Session,
    *,
    run_id: str,
    error_group: str | None = None,
    note: str | None = None,
) -> RunErrorTag:
    if error_group is not None and error_group not in ERROR_GROUPS:
        raise ValueError(f"Unknown error group: {error_group}")

    row = db.query(RunErrorTag).filter_by(run_id=run_id).one_or_none()
    if row is None:
        row = RunErrorTag(run_id=run_id)
        db.add(row)

    if error_group is not None:
        row.error_group = error_group
    if note is not None:
        row.note = note

    commit_with_retry(db)
    return row


def error_tag_to_dict(row: RunErrorTag) -> dict[str, str]:
    return {
        "run_id": row.run_id,
        "error_group": row.error_group,
        "error_group_label": error_group_label(row.error_group),
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
