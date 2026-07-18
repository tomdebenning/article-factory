from __future__ import annotations

from datetime import datetime, timezone

import article_factory.db as db_module
from article_factory.models import CompletedArticle, ShiftDeskSlot, ShiftPlan
from article_factory.services.flow_defaults import build_ai_news_desk_flow
from article_factory.services.flow_storage import ensure_default_templates, flows_root, write_flow
from article_factory.services.onboarding import (
    has_completed_first_shift,
    has_user_desk,
    morning_shift_onboarding,
)


def test_ensure_default_templates_seeds_beat_desks(configured_db) -> None:
    ensure_default_templates()
    templates = {path.name for path in (flows_root() / "_templates").glob("*.flow.json")}
    assert "sports.flow.json" in templates
    assert "business-news.flow.json" in templates
    assert "tech-news.flow.json" in templates
    assert "ai-news.flow.json" in templates


def test_onboarding_wizard_steps(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        payload = morning_shift_onboarding(db, setup_complete=False)
        assert payload["show_wizard"] is True
        assert payload["completed"] is False
        assert [step["id"] for step in payload["steps"]] == ["settings", "desk", "plan", "activate"]
        assert payload["steps"][0]["ok"] is False
        assert payload["steps"][1]["ok"] is True
    finally:
        db.close()


def test_onboarding_marks_completed_after_publish(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            CompletedArticle(
                run_id="run-onboard-1",
                title="Morning lead",
                body_markdown="# Hello",
                topic_slug="general",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        payload = morning_shift_onboarding(db, setup_complete=True)
        assert payload["completed"] is True
        assert payload["show_wizard"] is False
        assert has_completed_first_shift(db) is True
    finally:
        db.close()


def test_onboarding_desk_and_plan_progress(configured_db) -> None:
    write_flow("ai-news/desk.flow.json", build_ai_news_desk_flow())
    assert has_user_desk() is True

    db = db_module.SessionLocal()
    try:
        plan = ShiftPlan(
            shift_key="morning",
            window_starts_at=datetime(2026, 7, 17, 6, tzinfo=timezone.utc),
            window_ends_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
            status="draft",
        )
        db.add(plan)
        db.flush()
        db.add(
            ShiftDeskSlot(
                shift_plan_id=plan.id,
                name="AI News",
                desk_path="ai-news/desk.flow.json",
            )
        )
        db.commit()

        payload = morning_shift_onboarding(db, setup_complete=True)
        desk = next(step for step in payload["steps"] if step["id"] == "desk")
        plan_step = next(step for step in payload["steps"] if step["id"] == "plan")
        assert desk["ok"] is True
        assert plan_step["ok"] is True
    finally:
        db.close()


def test_factory_status_includes_onboarding(client, api_headers) -> None:
    response = client.get("/api/factory/status", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert "onboarding" in body
    assert body["onboarding"]["show_wizard"] is True
    assert len(body["onboarding"]["steps"]) == 4
