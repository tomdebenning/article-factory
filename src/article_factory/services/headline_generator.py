"""Generate Edition headlines after Editor accept, before publish."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import FactoryRun
from article_factory.services.article_text import headline_from_markdown
from article_factory.services.control_plane_completion import extract_json_object, run_control_plane_completion
from article_factory.services.runtime_settings import load_runtime_settings

logger = logging.getLogger(__name__)


async def generate_edition_headline(
    db: Session,
    *,
    draft: str,
    run: FactoryRun,
) -> str:
    """Return a headline for The Edition cards and article H1."""
    fallback = headline_from_markdown(draft)
    if db is None:
        return fallback
    runtime = load_runtime_settings(db)
    model = (runtime.default_model or run.selected_model or "").strip()
    puller = (run.selected_puller or "").strip()
    cp_url = (runtime.control_plane_url or "").strip()
    if not model or not puller or not cp_url:
        return fallback

    messages = [
        {
            "role": "system",
            "content": (
                "You are a headline editor for a digital newspaper. "
                'Return JSON only: {"headline": "Your headline here"}'
            ),
        },
        {
            "role": "user",
            "content": (
                "Write one clear, specific headline (max 12 words) for this approved article. "
                "Do not use quotes around the headline.\n\n"
                f"{draft[:12000]}"
            ),
        },
    ]
    cp = ControlPlaneClient(base_url=cp_url)
    try:
        raw = await run_control_plane_completion(
            cp=cp,
            puller=puller,
            model=model,
            messages=messages,
            agent_id="factory-headline-editor",
        )
        payload = extract_json_object(raw)
        headline = str(payload.get("headline") or "").strip()
        if headline:
            return headline[:256]
    except Exception as exc:
        logger.warning("Edition headline generation failed for run %s: %s", run.run_id, exc)
    return fallback
