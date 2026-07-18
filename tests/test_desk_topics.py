from __future__ import annotations

import pytest

from article_factory.services.assignment_desk import suggest_topics_for_desk
from article_factory.services.flow_storage import create_desk


@pytest.mark.asyncio
async def test_suggest_topics_for_desk_parses_assignments(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal

    async def fake_completion(*args, **kwargs):
        return '{"assignments": ["OSU spring game preview", "Transfer portal impact"]}'

    monkeypatch.setattr(
        "article_factory.services.assignment_desk.run_control_plane_completion",
        fake_completion,
    )

    db = SessionLocal()
    try:
        topics = await suggest_topics_for_desk(
            db,
            desk_path="desks/test-desk.flow.json",
            shift_key="morning",
            count=2,
            cp=object(),
            puller="gpu-01",
            model="test-model",
        )
        assert topics == ["OSU spring game preview", "Transfer portal impact"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_suggest_topics_for_desk_accepts_topics_key(configured_db, monkeypatch) -> None:
    from article_factory.db import SessionLocal

    async def fake_completion(*args, **kwargs):
        return '{"topics": ["Tulsa startup funding", "OKC aerospace jobs"]}'

    monkeypatch.setattr(
        "article_factory.services.assignment_desk.run_control_plane_completion",
        fake_completion,
    )

    db = SessionLocal()
    try:
        topics = await suggest_topics_for_desk(
            db,
            desk_path="desks/test-desk.flow.json",
            shift_key="morning",
            count=2,
            cp=object(),
            puller="gpu-01",
            model="test-model",
        )
        assert topics == ["Tulsa startup funding", "OKC aerospace jobs"]
    finally:
        db.close()


def test_save_desk_topics_api(client, api_headers, configured_db, monkeypatch) -> None:
    desk_path, _flow = create_desk(
        folder="desks",
        slug="topic-desk",
        display_name="Topic Desk",
        beat_brief="College football coverage.",
        edition_topic_slug="sports",
    )

    saved = client.post(
        "/api/desks/save-topics",
        headers=api_headers,
        json={
            "desk_path": desk_path,
            "shift_key": "morning",
            "topics": ["Story one", "Story two"],
            "merge": False,
        },
    )
    assert saved.status_code == 200
    order = saved.json()["order"]
    assert order["topics"] == ["Story one", "Story two"]

    merged = client.post(
        "/api/desks/save-topics",
        headers=api_headers,
        json={
            "desk_path": desk_path,
            "shift_key": "morning",
            "topics": ["Story three"],
            "merge": True,
        },
    )
    assert merged.status_code == 200
    assert merged.json()["order"]["topics"] == ["Story one", "Story two", "Story three"]


@pytest.mark.asyncio
async def test_desk_test_run_api(client, api_headers, configured_db, monkeypatch) -> None:
    desk_path, _flow = create_desk(
        folder="desks",
        slug="run-desk",
        display_name="Run Desk",
        beat_brief="Testing desk.",
        edition_topic_slug="sports",
    )

    async def fake_pipeline(db, **kwargs):
        from article_factory.models import FactoryRun

        run = FactoryRun(
            run_id="test-run-1",
            topic_slug=kwargs.get("topic_slug") or "sports",
            flow_path=kwargs.get("flow_path") or desk_path,
            status="running",
            current_step="step_1",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    from article_factory.services.runtime_settings import RuntimeSettings

    def fake_runtime(_db):
        return RuntimeSettings(
            control_plane_url="http://cp.test",
            cms_url="http://cms.test",
            cms_api_key="key",
            default_puller="",
            default_model="test-model",
            default_flow_path="sports/standard-4-step.flow.json",
        )

    monkeypatch.setattr("article_factory.routes.desks.load_runtime_settings", fake_runtime)

    async def fake_resolve(*args, **kwargs):
        return object(), "puller-01"

    async def fake_ensure_running():
        return None

    monkeypatch.setattr("article_factory.routes.desks._resolve_puller_for_model", fake_resolve)
    monkeypatch.setattr("article_factory.routes.desks.factory_loop.ensure_running", fake_ensure_running)
    monkeypatch.setattr("article_factory.routes.desks.schedule_pipeline_for_topic", fake_pipeline)

    response = client.post(
        "/api/desks/test-run",
        headers=api_headers,
        json={"desk_path": desk_path, "prompt": "Cover the spring game"},
    )
    assert response.status_code == 200
    assert response.json()["run"]["run_id"] == "test-run-1"
