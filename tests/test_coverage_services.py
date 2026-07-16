from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import article_factory.db as db_module
from article_factory.cms_client import CmsClient, CmsRequestError, cms_error_message
from article_factory.models import CompletedArticle, FactoryRun
from article_factory.services.showroom_publish import build_publish_payload, publish_article_to_showroom, slugify_title
from article_factory.services.step_tools import (
    WorkspaceViolation,
    augment_system_prompt_for_tools,
    build_step_tool_definitions,
    normalize_step_enabled_tools,
    resolve_step_tools,
    run_workspace_root,
)
from article_factory.services.verdict import extract_feedback_body
from article_factory.services.queue_presets import write_queue_preset, read_queue_preset, delete_queue_preset
from article_factory.services.personas import create_persona, delete_persona, list_personas, read_persona
from article_factory.services.step_tools import resolve_workspace_path


def test_slugify_title_fallback() -> None:
    assert slugify_title("!!!") == "article"


def test_cms_error_message_json_detail() -> None:
    response = MagicMock()
    response.status_code = 400
    response.request = MagicMock(method="POST", url=MagicMock(path="/x"))
    response.json.return_value = {"detail": "bad request"}
    assert "bad request" in cms_error_message(response)


def test_cms_error_message_non_json() -> None:
    response = MagicMock()
    response.status_code = 500
    response.request = MagicMock(method="GET", url=MagicMock(path="/health"))
    response.json.side_effect = ValueError("no json")
    assert "500" in cms_error_message(response)


@pytest.mark.asyncio
async def test_cms_client_raises_on_error() -> None:
    client = CmsClient(base_url="http://cms.test", api_key="key")
    bad = MagicMock()
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        "err", request=MagicMock(), response=MagicMock(status_code=502, json=lambda: {"detail": "down"})
    )
    bad.json.return_value = {"detail": "down"}
    bad.status_code = 502
    bad.request = MagicMock(method="POST", url=MagicMock(path="/internal/runs/complete"))

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=bad)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.cms_client.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(CmsRequestError):
            await client.post_run_complete({})


def test_build_publish_payload(configured_db) -> None:
    run = FactoryRun(
        run_id="run-pub",
        topic_slug="sports",
        status="completed",
        selected_model="m1",
        selected_puller="p1",
    )
    article = CompletedArticle(
        run_id="run-pub",
        topic_slug="sports",
        title="Old",
        summary="S",
        body_markdown="# Headline\n\nBody text",
        manifest={},
    )
    payload = build_publish_payload(run, article)
    assert payload["article"]["title"] == "Headline Body text"
    assert payload["manifest"]["selected_model"] == "m1"


@pytest.mark.asyncio
async def test_publish_article_empty_raises(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="run-empty-pub", topic_slug="sports", status="completed")
        article = CompletedArticle(
            run_id="run-empty-pub",
            topic_slug="sports",
            title="",
            summary="",
            body_markdown="   ",
            manifest={},
        )
        db.add(run)
        db.add(article)
        db.commit()
        with pytest.raises(CmsRequestError, match="empty"):
            await publish_article_to_showroom(db, run=run, article=article)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_publish_article_no_cms_config(configured_db) -> None:
    from article_factory.services.runtime_settings import update_factory_settings

    db = db_module.SessionLocal()
    try:
        update_factory_settings(db, {"cms_url": "", "cms_api_key": ""})
        run = FactoryRun(run_id="run-no-cms", topic_slug="sports", status="completed")
        article = CompletedArticle(
            run_id="run-no-cms",
            topic_slug="sports",
            title="T",
            summary="S",
            body_markdown="# T\n\nBody",
            manifest={},
        )
        db.add(run)
        db.add(article)
        db.commit()
        with pytest.raises(CmsRequestError, match="not configured"):
            await publish_article_to_showroom(db, run=run, article=article)
    finally:
        db.close()


def test_step_tools_normalize_and_definitions() -> None:
    enabled = normalize_step_enabled_tools({"web_search": True, "web_fetch": False})
    assert enabled["web_search"] is True
    assert resolve_step_tools(enabled)["web_search"] is True
    defs = build_step_tool_definitions(resolve_step_tools(None))
    names = [d["function"]["name"] for d in defs]
    assert "web_search" in names
    augmented = augment_system_prompt_for_tools("Base prompt", resolve_step_tools(None))
    assert "Factory tools" in augmented


def test_resolve_workspace_path_rejects_escape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    workspace = tmp_path / "run-path-check" / "workspace"
    workspace.mkdir(parents=True)
    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path(workspace, "../outside.txt")


def test_extract_feedback_body_strips_verdict() -> None:
    body = extract_feedback_body("Please fix intro.\n\nVERDICT: REJECT")
    assert body == "Please fix intro."


def test_personas_crud(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        created = create_persona(
            db,
            {"name": "Test P", "slug": "test-p", "style_prompt": "Write like test."},
        )
        assert created["slug"] == "test-p"
        assert read_persona(db, "test-p") is not None
        assert any(p["slug"] == "test-p" for p in list_personas(db))
        delete_persona(db, "test-p")
        db.commit()
        with pytest.raises(LookupError):
            read_persona(db, "test-p")
    finally:
        db.close()


def test_queue_preset_round_trip(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        rel_path, _ = __import__(
            "article_factory.services.flow_storage", fromlist=["create_flow"]
        ).create_flow(folder="", slug="preset-flow", display_name="Preset", step_count=1)
        preset = write_queue_preset(
            db,
            {
                "name": "My Preset",
                "slug": "my-preset",
                "topic_slug": "sports",
                "flow_path": rel_path,
                "topics": ["Topic one"],
            },
        )
        db.commit()
        loaded = read_queue_preset(db, "my-preset")
        assert loaded["name"] == "My Preset"
        deleted = delete_queue_preset(db, "my-preset")
        db.commit()
        assert deleted["slug"] == "my-preset"
    finally:
        db.close()
