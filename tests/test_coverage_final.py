"""Additional targeted tests to reach 97% coverage."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

import article_factory.db as db_module
from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import (
    CompletedArticle,
    FactoryRun,
    FlowQueue,
    StepExecution,
    TopicQueueItem,
)
from article_factory.services.flow_schema import FlowDefinition, new_flow_step
from article_factory.services.flow_storage import save_step_response_to_disk, write_flow
from article_factory.services.review_parser import (
    BEGIN_REVIEW_JSON,
    CRITERION_SPECS,
    END_REVIEW_JSON,
    parse_structured_review,
)
from article_factory.workers.base import StepContext
from article_factory.workers.executor import (
    NoPullerAvailableError,
    _no_puller_error_message,
    _response_timeout_error_message,
    _task_status_context,
    execute_step,
    run_step_from_context,
)


def _criteria_payload(**overrides: object) -> dict:
    base = {
        key: {"score": max_score - 1, "max_score": max_score}
        for key, _label, max_score in CRITERION_SPECS
    }
    base.update(overrides)
    return base


@pytest.fixture
def flow_runner_env(configured_db, monkeypatch) -> None:
    async def fake_select(_cp, model: str) -> str:
        return "puller-1"

    monkeypatch.setattr("article_factory.orchestrator.flow_runner.select_puller_for_model", fake_select)


def _review_json_text(**overrides: object) -> str:
    payload = {
        "schema_version": 1,
        "total_score": 88,
        "verdict": "accepted",
        "criteria": _criteria_payload(),
        "previous_issues": [],
        "required_changes": [],
    }
    payload.update(overrides)
    return f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}\nVERDICT: ACCEPT"


# --- review_parser remaining branches ---


def test_normalize_issue_status_branches() -> None:
    from article_factory.services.review_parser import _normalize_issue_status

    assert _normalize_issue_status("partial fix") == "partially_fixed"
    assert _normalize_issue_status("needs fix") == "fixed"
    assert _normalize_issue_status("regressed badly") == "regressed"
    assert _normalize_issue_status("not fixed yet") == "not_fixed"
    assert _normalize_issue_status("weird") == "unknown"


def test_review_parser_criteria_must_be_object() -> None:
    text = _review_json_text(criteria="bad")
    review = parse_structured_review(text)
    assert review is not None
    assert any("criteria must be an object" in w for w in review.parse_warnings)


def test_review_parser_max_score_mismatch_warning() -> None:
    criteria = _criteria_payload()
    criteria["accuracy_verifiable_facts"] = {"score": 30, "max_score": 99}
    text = _review_json_text(criteria=criteria)
    review = parse_structured_review(text)
    assert review is not None
    assert any("max_score expected" in w for w in review.parse_warnings)


def test_review_parser_accepted_with_required_changes() -> None:
    text = _review_json_text(
        required_changes=[{"issue_number": 1, "category": "x", "status": "new", "problem": "p"}]
    )
    review = parse_structured_review(text)
    assert review is not None
    assert any("required_changes" in w for w in review.parse_warnings)


def test_review_parser_json_block_not_object() -> None:
    text = f"{BEGIN_REVIEW_JSON}\n[]\n{END_REVIEW_JSON}\nVERDICT: ACCEPT"
    review = parse_structured_review(text)
    assert review is not None
    assert any("must be an object" in w for w in review.parse_warnings)


def test_review_parser_criterion_key_from_label() -> None:
    from article_factory.services.review_parser import _criterion_key_from_label

    assert _criterion_key_from_label("Accuracy & Verifiable Facts") == "accuracy_verifiable_facts"
    assert _criterion_key_from_label("unknown label") is None


def test_review_parser_legacy_criterion_line_fallback() -> None:
    lines = [
        "Accuracy & Verifiable Facts",
        "30 / 40",
        "Organization & Flow: 10 / 15",
    ]
    text = "\n".join(lines) + "\nVERDICT: ACCEPT"
    review = parse_structured_review(text)
    assert review is not None
    assert review.source == "legacy"
    assert review.criteria


def test_review_parser_issues_from_non_list() -> None:
    from article_factory.services.review_parser import _parse_issues_from_json

    assert _parse_issues_from_json("not-a-list", required=False) == []
    assert _parse_issues_from_json([{"status": "fixed", "problem": "p"}], required=True)[0].required_change == "p"


def test_review_parser_legacy_criteria_only_source() -> None:
    text = (
        "Organization & Flow\n12 / 15\n"
        "Writing Quality: 14 / 15\n"
        "VERDICT: ACCEPT"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.source == "legacy"


# --- executor remaining branches ---


def test_task_status_context_fields() -> None:
    ctx = _task_status_context(
        {
            "status": "failed",
            "queue_depth_at_submit": 2,
            "fetched_by": "puller-a",
            "fetched_at": "2026-01-01T00:00:00Z",
            "response_error": "boom",
        }
    )
    assert "queue depth" in ctx
    assert "fetched by" in ctx
    assert "puller error" in ctx


def test_no_puller_error_messages() -> None:
    busy = _no_puller_error_message(
        step_key="writer",
        puller="p1",
        model="m1",
        attempts=2,
        puller_was_alive=True,
        task_status={"status": "queued"},
    )
    assert "heartbeating" in busy

    backed_up = _no_puller_error_message(
        step_key="writer",
        puller="p1",
        model="m1",
        attempts=2,
        puller_was_alive=True,
        task_status={"status": "fetched"},
    )
    assert "backed up" in backed_up


def test_response_timeout_error_messages() -> None:
    fetched = _response_timeout_error_message(
        step_key="writer",
        puller="p1",
        puller_was_alive=False,
        task_status={"status": "fetched", "fetched_by": "p1"},
    )
    assert "fetched the task" in fetched

    failed = _response_timeout_error_message(
        step_key="writer",
        puller="p1",
        puller_was_alive=False,
        task_status={"status": "failed", "fetched_by": "p1"},
    )
    assert "failed on puller" in failed

    stale = _response_timeout_error_message(
        step_key="writer",
        puller="p1",
        puller_was_alive=True,
        task_status={"status": "unknown"},
    )
    assert "stopped heartbeating" in stale


@pytest.mark.asyncio
async def test_execute_step_poll_cancelled(configured_db, monkeypatch) -> None:
    from article_factory.models import FactoryRun
    from article_factory.services.run_control import request_run_cancel
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="poll-cancel", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="poll-cancel", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    await request_run_cancel("poll-cancel")

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value=None)
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        from article_factory.services.run_control import RunCancelledError

        with pytest.raises(RunCancelledError):
            await execute_step(
                cp,
                step_key="writer",
                system_prompt="sys",
                user_content="user",
                puller="p",
                model="m",
                run_id="poll-cancel",
                tracer=tracer,
            )


@pytest.mark.asyncio
async def test_execute_step_puller_alive_waiting_message(configured_db, monkeypatch) -> None:
    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 30.0)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 0.05)

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="alive-wait", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="alive-wait", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.get_puller = AsyncMock(return_value={"puller_name": "p"})

    with patch("article_factory.workers.executor.get_registered_puller_on_cp", new=AsyncMock(return_value={"puller_name": "p"})):
        with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(NoPullerAvailableError):
                await execute_step(
                    cp,
                    step_key="writer",
                    system_prompt="sys",
                    user_content="user",
                    puller="p",
                    model="m",
                    tracer=tracer,
                )

    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_execute_step_puller_stale_during_response(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_response_timeout_seconds", 0.02)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)
    monkeypatch.setattr(settings, "step_puller_stale_grace_seconds", 0.01)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 0.05)

    poll_count = 0

    async def poll_side_effect(*_args, **_kwargs):
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            return [{"status": "fetched"}]
        if poll_count >= 3:
            return [
                {
                    "message": {"content": "done"},
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }
            ]
        return []

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)
    cp.task_was_fetched = AsyncMock(return_value=True)

    puller_alive = True

    async def puller_check(*_args, **_kwargs):
        nonlocal puller_alive
        if poll_count > 2:
            puller_alive = False
        return {"puller_name": "p"} if puller_alive else None

    with patch("article_factory.workers.executor.get_registered_puller_on_cp", new=AsyncMock(side_effect=puller_check)):
        with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
            result = await execute_step(
                cp,
                step_key="writer",
                system_prompt="sys",
                user_content="user",
                puller="p",
                model="m",
            )
    assert result["content"] == "done"


@pytest.mark.asyncio
async def test_execute_step_response_timeout_with_tracer(configured_db, monkeypatch) -> None:
    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_response_timeout_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="resp-timeout", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="resp-timeout", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "fetched", "fetched_by": "p"})
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=True)

    with patch("article_factory.workers.executor.get_registered_puller_on_cp", new=AsyncMock(return_value=None)):
        with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(TimeoutError):
                await execute_step(
                    cp,
                    step_key="writer",
                    system_prompt="sys",
                    user_content="user",
                    puller="p",
                    model="m",
                    tracer=tracer,
                )

    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_execute_step_review_empty_retry_suffix(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_empty_response_max_attempts", 2)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)

    responses = [
        [{"message": {"content": "   "}, "usage": {}}],
        [{"message": {"content": "VERDICT: ACCEPT"}, "usage": {}}],
    ]

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "completed"})
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(side_effect=responses)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="review",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
        )

    assert "VERDICT" in result["content"]
    retry_message = cp.submit_task.await_args_list[-1].args[0]["messages"][-1]["content"]
    assert "VERDICT: ACCEPT" in retry_message


@pytest.mark.asyncio
async def test_execute_step_tool_round_with_tracer(configured_db, monkeypatch, tmp_path) -> None:
    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="tool-tracer", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="tool-tracer", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        side_effect=[
            [
                {
                    "message": {
                        "content": "",
                        "thinking": "planning",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({"path": "a.txt", "content": "x"}),
                                },
                            }
                        ],
                    },
                    "usage": {},
                }
            ],
            [{"message": {"content": "final"}, "usage": {}}],
        ]
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
            run_id="tool-tracer",
            tracer=tracer,
            enabled_tools={"write_file": True, "read_file": False, "web_search": False, "web_fetch": False},
        )

    assert result["content"] == "final"
    assert tracer.execution.status == "completed"


@pytest.mark.asyncio
async def test_execute_step_empty_after_retries_with_tracer(configured_db, monkeypatch) -> None:
    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_empty_response_max_attempts", 1)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="empty-tracer", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="empty-tracer", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "completed"})
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(return_value=[{"message": {"content": "  "}, "usage": {}}])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError, match="empty content"):
            await execute_step(
                cp,
                step_key="writer",
                system_prompt="sys",
                user_content="user",
                puller="p",
                model="m",
                tracer=tracer,
            )

    assert tracer.execution.status == "failed"


# --- step_trace enrich fallback ---


def test_enrich_steps_with_responses_by_key_bucket(configured_db) -> None:
    from article_factory.services.step_trace import enrich_steps_with_responses

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="enrich-bucket",
            topic_slug="sports",
            status="completed",
            pipeline_state={
                "step_records": [
                    {"step_key": "writer", "content": "draft one"},
                    {"step_key": "writer", "content": "draft two"},
                ]
            },
        )
        db.add(run)
        db.commit()

        steps = [
            {"step_key": "writer", "status": "completed"},
            {"step_key": "writer", "status": "completed"},
        ]
        enriched = enrich_steps_with_responses(db, "enrich-bucket", steps)
        assert enriched[0]["response_content"] == "draft one"
        assert enriched[1]["response_content"] == "draft two"
    finally:
        db.close()


def test_batch_step_executions_payload(configured_db) -> None:
    from article_factory.services.step_trace import batch_step_executions_payload

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="batch-a", topic_slug="sports", status="completed"))
        db.add(FactoryRun(run_id="batch-b", topic_slug="sports", status="completed"))
        db.flush()
        db.add(
            StepExecution(
                run_id="batch-a",
                step_key="writer",
                status="completed",
                response_content="A",
                puller="p",
                model="m",
            )
        )
        db.add(
            StepExecution(
                run_id="batch-b",
                step_key="writer",
                status="completed",
                response_content="B",
                puller="p",
                model="m",
            )
        )
        db.commit()
        assert batch_step_executions_payload(db, []) == {}
        grouped = batch_step_executions_payload(db, ["batch-a", "batch-b"])
        assert grouped["batch-a"][0]["response_content"] == "A"
        assert grouped["batch-b"][0]["response_content"] == "B"
    finally:
        db.close()


# --- flow_queues routes ---


def test_flow_queue_preset_get_value_error(client, api_headers) -> None:
    response = client.get("/api/flow-queues/presets/%20%20", headers=api_headers)
    assert response.status_code == 400


def test_flow_queue_start_disabled_queue(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="start-q", display_name="Start Q", step_count=1)
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "name": "Disabled Queue",
            "flow_path": rel_path,
            "topic_slug": "sports",
            "default_model": "model-a",
            "topics": ["Topic A"],
            "enabled": False,
        },
    )
    assert response.status_code in {200, 410}
    if response.status_code == 200:
        assert response.json()["queue"]["enabled"] is False


def test_flow_queue_post_value_error(client, api_headers) -> None:
    response = client.post(
        "/api/flow-queues",
        headers=api_headers,
        json={"name": "", "flow_path": "missing.flow.json", "topic_slug": "sports"},
    )
    assert response.status_code == 400


def test_flow_queue_put_value_error(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="put-q", display_name="Put Q", step_count=1)
        queue = create_flow_queue(db, name="Put Queue", flow_path=rel_path, topic_slug="sports")
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    response = client.put(
        f"/api/flow-queues/{queue_id}",
        headers=api_headers,
        json={"name": "   "},
    )
    assert response.status_code == 400


def test_flow_queue_delete_running_blocked(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="del-q", display_name="Del Q", step_count=1)
        queue = create_flow_queue(db, name="Del Queue", flow_path=rel_path, topic_slug="sports")
        db.add(TopicQueueItem(flow_queue_id=queue.id, topic_slug="sports", flow_path=rel_path, prompt="x", status="running"))
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    response = client.delete(f"/api/flow-queues/{queue_id}", headers=api_headers)
    assert response.status_code == 400


def test_flow_queue_enqueue_disabled(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="enq-q", display_name="Enq Q", step_count=1)
        queue = create_flow_queue(db, name="Enq Queue", flow_path=rel_path, topic_slug="sports", enabled=False)
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    response = client.post(
        f"/api/flow-queues/{queue_id}/enqueue",
        headers=api_headers,
        json={"topics": ["Topic"]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_flow_queue_stop_and_clear_not_found(client, api_headers) -> None:
    response = client.post("/api/flow-queues/99999/stop-and-clear", headers=api_headers)
    assert response.status_code == 404


# --- flow_queues service ---


def test_update_flow_queue_empty_name(configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue, update_flow_queue
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="upd-q", display_name="Upd", step_count=1)
        queue = create_flow_queue(db, name="Upd Queue", flow_path=rel_path, topic_slug="sports")
        db.commit()
        with pytest.raises(ValueError, match="cannot be empty"):
            update_flow_queue(db, queue.id, name="   ")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_stop_and_clear_nothing_message(configured_db, monkeypatch) -> None:
    from article_factory.services.flow_queues import create_flow_queue, stop_and_clear_flow_queue
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="clear-q", display_name="Clear", step_count=1)
        queue = create_flow_queue(db, name="Empty Queue", flow_path=rel_path, topic_slug="sports")
        db.commit()
        monkeypatch.setattr("article_factory.orchestrator.runner.factory_loop.cancel_run_workers", lambda **kwargs: None)
        monkeypatch.setattr("article_factory.orchestrator.runner.factory_loop.request_dispatch", lambda: None)
        result = await stop_and_clear_flow_queue(db, queue_id=queue.id)
        assert "nothing to stop" in result["message"]
    finally:
        db.close()


# --- admin routes ---


def test_get_article_step_file_errors(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            CompletedArticle(
                run_id="art-files",
                topic_slug="sports",
                title="T",
                body_markdown="Body",
            )
        )
        db.commit()
    finally:
        db.close()

    assert client.get("/api/articles/art-files/step-files/missing.md", headers=api_headers).status_code == 404
    assert client.get("/api/articles/art-files/step-files/../evil.md", headers=api_headers).status_code == 400


def test_get_article_workspace_file_errors(client, api_headers, configured_db) -> None:
    db = db_module.SessionLocal()
    try:
        db.add(
            CompletedArticle(
                run_id="ws-files",
                topic_slug="sports",
                title="T",
                body_markdown="Body",
            )
        )
        db.commit()
    finally:
        db.close()

    assert client.get("/api/articles/ws-files/workspace-files/missing.txt", headers=api_headers).status_code == 404


def test_resolve_queue_flow_path_from_queue_id(client, api_headers, configured_db) -> None:
    from article_factory.services.flow_queues import create_flow_queue
    from article_factory.services.flow_storage import create_flow

    db = db_module.SessionLocal()
    try:
        rel_path, _ = create_flow(folder="", slug="resolve-q", display_name="Resolve", step_count=1)
        queue = create_flow_queue(db, name="Resolve Queue", flow_path=rel_path, topic_slug="sports")
        db.commit()
        queue_id = queue.id
    finally:
        db.close()

    response = client.post(
        "/api/queue",
        headers=api_headers,
        json={"prompt": "Hello", "flow_queue_id": queue_id, "flow_path": ""},
    )
    assert response.status_code == 200


# --- telemetry ---


def test_load_flow_for_run_version_exception(configured_db, monkeypatch) -> None:
    from article_factory.services.telemetry import _load_flow_for_run

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="bad-version",
            topic_slug="sports",
            status="completed",
            flow_version_id=99999,
        )
        db.add(run)
        db.commit()
        assert _load_flow_for_run(db, run) is None
    finally:
        db.close()


def test_records_from_run_step_executions(configured_db) -> None:
    from article_factory.services.telemetry import _records_from_run

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(run_id="rec-exec", topic_slug="sports", status="completed")
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="rec-exec",
                step_key="writer",
                status="completed",
                response_content="text",
                puller="p",
                model="m",
            )
        )
        db.commit()
        records = _records_from_run(db, run)
        assert records[0]["content"] == "text"
    finally:
        db.close()


def test_usage_tokens_helper() -> None:
    from article_factory.services.telemetry import _usage_tokens

    assert _usage_tokens(None) == (None, None, None)
    assert _usage_tokens({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}) == (1, 2, 3)


# --- showroom_status_sync ---


@pytest.mark.asyncio
async def test_refresh_showroom_operational_error_retry(configured_db, monkeypatch) -> None:
    from article_factory.services import showroom_status_sync as sync_mod
    from article_factory.services.showroom_status_sync import refresh_showroom_status

    sync_mod._push_in_flight = False
    sync_mod._last_push_at = 0.0

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(
            db,
            {"cms_url": "http://cms.test", "cms_api_key": "cms-key"},
        )
        db.commit()
    finally:
        db.close()

    attempts = 0

    async def flaky_push(_db, _cms) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("stmt", {}, Exception("database is locked"))

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        flaky_push,
    )
    monkeypatch.setattr("article_factory.services.showroom_status_sync.time.sleep", lambda _s: None)

    assert await refresh_showroom_status() is True
    assert attempts == 2


@pytest.mark.asyncio
async def test_showroom_status_tick_operational_error(configured_db, monkeypatch) -> None:
    from article_factory.services.showroom_status_sync import showroom_status_tick

    db = db_module.SessionLocal()
    try:
        from article_factory.services.runtime_settings import update_factory_settings

        update_factory_settings(db, {"cms_url": "http://cms.test", "cms_api_key": "key"})
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.push_showroom_factory_status",
        AsyncMock(side_effect=OperationalError("stmt", {}, Exception("database is locked"))),
    )
    monkeypatch.setattr(
        "article_factory.services.showroom_status_sync.refresh_showroom_status",
        AsyncMock(return_value=True),
    )

    db = db_module.SessionLocal()
    try:
        await showroom_status_tick(db)
    finally:
        db.close()


def test_schedule_showroom_status_refresh_no_loop() -> None:
    from article_factory.services.showroom_status_sync import schedule_showroom_status_refresh

    schedule_showroom_status_refresh(force=True)


# --- control_plane client ---


@pytest.mark.asyncio
async def test_control_plane_poll_responses_error() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=RuntimeError("network"))
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        assert await client.poll_responses("agent", conversation_id="conv") == []


@pytest.mark.asyncio
async def test_control_plane_submit_task_non_200() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")
    bad = MagicMock()
    bad.status_code = 503
    bad.text = "unavailable"

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=bad)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(RuntimeError, match="503"):
            await client.submit_task({"agent_id": "a"})


# --- flow_runner draft warning ---


@pytest.mark.asyncio
async def test_flow_runner_warns_missing_draft(configured_db, flow_runner_env, monkeypatch) -> None:
    from article_factory.orchestrator.flow_runner import execute_flow_pipeline
    from article_factory.services.runtime_settings import load_runtime_settings

    writer = new_flow_step(order=1, label="Writer", step_key="writer")
    review = new_flow_step(order=2, label="Review", step_key="review")
    flow = FlowDefinition(
        slug="draft-warn",
        display_name="Draft Warn",
        article_step_id=writer.step_id,
        steps=[writer, review],
    )
    write_flow("draft-warn.flow.json", flow)

    async def fake_step(ctx, cp=None, tracer=None, run_id=None):
        return {
            "step_key": ctx.step_key,
            "step_name": ctx.label,
            "content": "VERDICT: ACCEPT" if ctx.step_key == "review" else "",
            "duration_ms": 1,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "completed_at": "2026-01-01T00:00:00Z",
        }

    completed = AsyncMock()
    monkeypatch.setattr("article_factory.orchestrator.flow_runner.run_step_from_context", fake_step)

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="draft-warn-run",
            topic_slug="general",
            flow_path="draft-warn.flow.json",
            status="running",
            selected_model="m",
            selected_puller="p",
        )
        db.add(run)
        db.commit()
        await execute_flow_pipeline(
            db,
            run=run,
            flow_path="draft-warn.flow.json",
            topic_prompt="Topic",
            runtime=load_runtime_settings(db),
            cms=None,
            emit_step_started=AsyncMock(),
            complete_run=completed,
        )
        completed.assert_awaited()
    finally:
        db.close()


# --- app lifespan branches ---


def test_app_lifespan_migrated_presets_log(configured_db, tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from article_factory.app import create_app
    from article_factory.orchestrator.runner import factory_loop

    presets_dir = tmp_path / "queue-presets"
    presets_dir.mkdir()
    monkeypatch.setattr("article_factory.services.queue_presets.queue_presets_root", lambda: presets_dir)
    (presets_dir / "legacy.queue.json").write_text(
        json.dumps(
            {
                "name": "Legacy",
                "slug": "legacy",
                "topic_slug": "sports",
                "flow_path": "default.flow.json",
                "default_model": "m",
                "topics": ["T"],
            }
        ),
        encoding="utf-8",
    )

    async def noop() -> None:
        return None

    monkeypatch.setattr(factory_loop, "start", noop)
    monkeypatch.setattr(factory_loop, "stop", noop)
    monkeypatch.setattr("article_factory.app.control_plane_heartbeat_loop.start", noop)
    monkeypatch.setattr("article_factory.app.control_plane_heartbeat_loop.stop", noop)
    monkeypatch.setattr("article_factory.app.showroom_status_loop.start", noop)
    monkeypatch.setattr("article_factory.app.showroom_status_loop.stop", noop)
    monkeypatch.setattr("article_factory.app.prompt_improvement_runner.start", noop)

    with patch("article_factory.app.assess_factory_readiness", new=AsyncMock(return_value={"setup_complete": True, "issue_checks": []})):
        with patch(
            "article_factory.services.showroom_status_sync.refresh_showroom_status",
            new=AsyncMock(return_value=True),
        ):
            with TestClient(create_app()) as test_client:
                assert test_client.get("/api/health").status_code == 200


def test_app_lifespan_push_showroom_when_busy(configured_db, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from article_factory.app import create_app
    from article_factory.orchestrator.runner import factory_loop

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="busy-run", topic_slug="sports", status="running"))
        db.commit()
    finally:
        db.close()

    async def noop() -> None:
        return None

    monkeypatch.setattr(factory_loop, "start", noop)
    monkeypatch.setattr(factory_loop, "stop", noop)
    monkeypatch.setattr("article_factory.app.control_plane_heartbeat_loop.start", noop)
    monkeypatch.setattr("article_factory.app.control_plane_heartbeat_loop.stop", noop)
    monkeypatch.setattr("article_factory.app.showroom_status_loop.start", noop)
    monkeypatch.setattr("article_factory.app.showroom_status_loop.stop", noop)
    monkeypatch.setattr("article_factory.app.prompt_improvement_runner.start", noop)

    refresh = AsyncMock(return_value=True)
    with patch("article_factory.app.assess_factory_readiness", new=AsyncMock(return_value={"setup_complete": True, "issue_checks": []})):
        with patch("article_factory.services.showroom_status_sync.refresh_showroom_status", refresh):
            with TestClient(create_app()) as test_client:
                assert test_client.get("/api/health").status_code == 200
            assert refresh.await_count >= 1
