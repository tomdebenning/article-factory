from __future__ import annotations

import csv
import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from article_factory.config import settings
from article_factory.models import SavedQueue
from article_factory.services.flow_queues import slugify_queue_name

logger = logging.getLogger(__name__)

PRESET_VERSION = 1


def queue_presets_root() -> Path:
    root = Path(settings.flows_root).parent / "queue-presets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_topics_text(content: str, *, filename: str = "") -> list[str]:
    """Parse topics from plain text (one per line) or CSV (first column per row)."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return parse_topics_csv(content)
    return parse_topics_lines(content)


def parse_topics_lines(content: str) -> list[str]:
    return [line.strip() for line in content.splitlines() if line.strip()]


def parse_topics_csv(content: str) -> list[str]:
    topics: list[str] = []
    reader = csv.reader(StringIO(content))
    for row in reader:
        if not row:
            continue
        first = str(row[0]).strip()
        if first:
            topics.append(first)
    return topics


def normalize_preset(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Queue name is required")
    slug = str(data.get("slug") or "").strip() or slugify_queue_name(name)
    topics_raw = data.get("topics") or []
    if not isinstance(topics_raw, list):
        raise ValueError("Queue topics must be a list")
    topics = [str(item).strip() for item in topics_raw if str(item).strip()]
    return {
        "version": PRESET_VERSION,
        "name": name,
        "slug": slugify_queue_name(slug),
        "topic_slug": (str(data.get("topic_slug") or "general").strip() or "general"),
        "flow_path": str(data.get("flow_path") or "").strip(),
        "default_model": str(data.get("default_model") or "").strip(),
        "topics": topics,
    }


def _unique_slug(db: Session, base_slug: str, *, exclude_id: int | None = None) -> str:
    candidate = base_slug
    suffix = 2
    while True:
        query = db.query(SavedQueue).filter_by(slug=candidate)
        if exclude_id is not None:
            query = query.filter(SavedQueue.id != exclude_id)
        if query.one_or_none() is None:
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1


def preset_summary(row: SavedQueue) -> dict[str, Any]:
    return {
        "slug": row.slug,
        "name": row.name,
        "topic_slug": row.topic_slug,
        "flow_path": row.flow_path,
        "default_model": row.default_model,
        "topic_count": len(row.topics or []),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def preset_payload(row: SavedQueue) -> dict[str, Any]:
    return {
        **preset_summary(row),
        "version": PRESET_VERSION,
        "topics": list(row.topics or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_queue_presets(db: Session) -> list[dict[str, Any]]:
    rows = db.query(SavedQueue).order_by(SavedQueue.name.asc()).all()
    return [preset_summary(row) for row in rows]


def read_queue_preset(db: Session, slug: str) -> dict[str, Any]:
    cleaned = slugify_queue_name(slug)
    row = db.query(SavedQueue).filter_by(slug=cleaned).one_or_none()
    if row is None:
        raise LookupError(f"Saved queue not found: {slug}")
    return preset_payload(row)


def write_queue_preset(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    preset = normalize_preset(data)
    if not preset["flow_path"]:
        raise ValueError("Flow path is required to save a queue")

    row = db.query(SavedQueue).filter_by(slug=preset["slug"]).one_or_none()

    if row is None:
        slug = _unique_slug(db, preset["slug"])
        row = SavedQueue(
            slug=slug,
            name=preset["name"],
            flow_path=preset["flow_path"],
            topic_slug=preset["topic_slug"],
            default_model=preset["default_model"],
            topics=preset["topics"],
        )
        db.add(row)
    else:
        row.name = preset["name"]
        row.flow_path = preset["flow_path"]
        row.topic_slug = preset["topic_slug"]
        row.default_model = preset["default_model"]
        row.topics = preset["topics"]

    db.flush()
    return preset_payload(row)


def delete_queue_preset(db: Session, slug: str) -> dict[str, str]:
    cleaned = slugify_queue_name(slug)
    row = db.query(SavedQueue).filter_by(slug=cleaned).one_or_none()
    if row is None:
        raise LookupError(f"Saved queue not found: {slug}")
    deleted = {"slug": row.slug, "name": row.name}
    db.delete(row)
    db.flush()
    return deleted


def migrate_file_presets_to_db(db: Session) -> int:
    """Import legacy data/queue-presets/*.queue.json files into the database."""
    root = queue_presets_root()
    if not root.is_dir():
        return 0

    imported = 0
    for path in sorted(root.glob("*.queue.json")):
        try:
            data = normalize_preset(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Skipping invalid queue preset file %s: %s", path.name, exc)
            continue

        existing = db.query(SavedQueue).filter_by(slug=data["slug"]).one_or_none()
        if existing is None:
            slug = _unique_slug(db, data["slug"])
            db.add(
                SavedQueue(
                    slug=slug,
                    name=data["name"],
                    flow_path=data["flow_path"],
                    topic_slug=data["topic_slug"],
                    default_model=data["default_model"],
                    topics=data["topics"],
                )
            )
            imported += 1
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Could not remove migrated preset file %s: %s", path.name, exc)

    if imported:
        db.flush()
        logger.info("Migrated %s queue preset(s) from disk into the database", imported)
    return imported
