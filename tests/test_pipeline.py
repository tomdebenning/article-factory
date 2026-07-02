from __future__ import annotations

import article_factory.db as db_module
from article_factory.models import FactoryRun
from article_factory.orchestrator.pipeline import (
    build_manifest,
    new_run_id,
    push_factory_status,
)
from article_factory.workers.base import render_prompt, review_accepted, review_feedback


def test_new_run_id() -> None:
    first = new_run_id()
    second = new_run_id()
    assert first.startswith("run-")
    assert first != second


def test_build_manifest(configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id=new_run_id(), topic_slug="sports", status="published")
        db.add(run)
        db.commit()
        steps = [
            {
                "step_key": "writer",
                "duration_ms": 100,
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                "tools_used": [
                    {"tool": "write_file", "label": "Write file", "detail": "draft.md", "round": 1, "ok": True}
                ],
            }
        ]
        manifest = build_manifest(run, steps)
        assert manifest["run_id"] == run.run_id
        assert manifest["stats"]["total_tokens"] == 30
        assert manifest["stats"]["llm_calls"] == 1
        assert manifest["stats"]["total_turns"] == 1
        assert len(manifest["tool_use"]) == 1
        assert manifest["tool_use"][0]["tools"] == ["Write file"]
    finally:
        db.close()


def test_render_prompt() -> None:
    result = render_prompt("Hello {{topic}} and {{draft}}", {"topic": "Sports", "draft": "Draft"})
    assert result == "Hello Sports and Draft"


def test_review_accepted() -> None:
    assert review_accepted("Detailed notes.\n\nVERDICT: ACCEPT") is True
    assert review_accepted("Needs work.\n\nVERDICT: REJECT") is False
    assert review_accepted("") is False


def test_review_feedback() -> None:
    assert review_feedback("Please fix intro.\n\nVERDICT: REJECT") == "Please fix intro."
    assert review_feedback("Looks good.\n\nVERDICT: ACCEPT") == "Looks good."


async def test_push_factory_status(configured_db) -> None:
    captured: list[dict] = []

    class FakeCms:
        async def put_factory_status(self, body: dict) -> None:
            captured.append(body)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="run-abc", topic_slug="sports", status="running", current_step="writer")
        db.add(run)
        db.commit()
        await push_factory_status(
            FakeCms(),
            db=db,
            state="running",
            active_run=run,
            active_runs=[run],
            queue_depth=2,
            topic_slug="sports",
        )
        assert captured[0]["state"] == "running"
        assert captured[0]["active_run"]["run_id"] == "run-abc"
        assert len(captured[0]["active_runs"]) == 1
        assert captured[0]["active_runs"][0]["run_id"] == "run-abc"

        await push_factory_status(
            FakeCms(),
            db=db,
            state="idle",
            active_run=None,
            active_runs=[],
            queue_depth=0,
        )
        assert captured[1]["active_run"] is None
        assert captured[1]["active_runs"] == []
    finally:
        db.close()
