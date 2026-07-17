from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from article_factory.cms_client import CmsClient, CmsRequestError, best_effort_showroom
from article_factory.models import CompletedArticle, FactoryRun
from article_factory.services.article_text import article_has_content, headline_from_markdown
from article_factory.services.run_attachments import collect_run_workspace_attachments
from article_factory.services.step_trace import merge_tools_into_manifest, step_executions_payload
from article_factory.services.token_usage import enrich_manifest
from article_factory.services.runtime_settings import RuntimeSettings, load_runtime_settings
from article_factory.services.showroom_status_sync import push_showroom_factory_status

logger = logging.getLogger(__name__)


def slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80] or "article"


def build_publish_payload(
    db: Session,
    run: FactoryRun,
    article: CompletedArticle,
) -> dict:
    merged = merge_tools_into_manifest(
        article.manifest or run.manifest or {},
        step_executions_payload(db, run.run_id),
    )
    manifest = enrich_manifest(
        merged,
        selected_model=run.selected_model,
        body_markdown=article.body_markdown,
    )
    if not manifest.get("selected_puller") and run.selected_puller:
        manifest = {**manifest, "selected_puller": run.selected_puller}
    title = headline_from_markdown(article.body_markdown)
    return {
        "run_id": run.run_id,
        "topic_slug": run.topic_slug,
        "article": {
            "slug": slugify_title(title),
            "title": title,
            "summary": article.summary,
            "body_markdown": article.body_markdown,
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        "manifest": manifest,
        "attachments": collect_run_workspace_attachments(run.run_id),
    }


async def publish_article_to_showroom(
    db: Session,
    *,
    run: FactoryRun,
    article: CompletedArticle,
    cms: CmsClient | None = None,
    runtime: RuntimeSettings | None = None,
) -> dict:
    """Push a completed article to Showroom CMS."""
    runtime = runtime or load_runtime_settings(db)
    if not article_has_content(article.body_markdown):
        raise CmsRequestError("Cannot publish an empty article to Showroom")
    if cms is None:
        if not runtime.cms_url.strip() or not runtime.cms_api_key.strip():
            raise CmsRequestError("Showroom CMS is not configured")
        cms = CmsClient(base_url=runtime.cms_url, api_key=runtime.cms_api_key)

    payload = build_publish_payload(db, run, article)
    article.manifest = payload["manifest"]
    run.manifest = payload["manifest"]
    article.title = payload["article"]["title"]
    db.commit()

    result = await cms.post_run_complete(payload)
    await best_effort_showroom(
        f"run_published event for {run.run_id}",
        lambda: cms.post_run_event(
            {
                "run_id": run.run_id,
                "topic_slug": run.topic_slug,
                "event": "run_published",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        ),
    )
    try:
        await push_showroom_factory_status(db, cms)
    except Exception:
        logger.warning("Showroom status push failed after publish", exc_info=True)
    return result
