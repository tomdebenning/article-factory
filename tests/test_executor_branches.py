from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.workers.executor import (
    NoPullerAvailableError,
    _assistant_message_dict,
    _no_puller_error_message,
    _response_timeout_error_message,
    _task_status_context,
    execute_step,
    run_step_from_context,
)
from article_factory.workers.base import StepContext
from article_factory.services.run_control import RunCancelledError


def test_assistant_message_dict_with_thinking() -> None:
    payload = _assistant_message_dict(
        {"content": "hi", "thinking": "reason", "tool_calls": [{"id": "1"}]}
    )
    assert payload["thinking"] == "reason"
    assert payload["tool_calls"] == [{"id": "1"}]


def test_task_status_context() -> None:
    assert _task_status_context(None) == ""
    text = _task_status_context(
        {
            "status": "failed",
            "queue_depth_at_submit": 2,
            "fetched_by": "gpu-01",
            "fetched_at": "2026-01-01",
            "response_error": "boom",
        }
    )
    assert "queue depth" in text
    assert "gpu-01" in text


def test_no_puller_error_message_variants() -> None:
    queued = _no_puller_error_message(
        step_key="writer",
        puller="p1",
        model="m1",
        attempts=3,
        puller_was_alive=True,
        task_status={"status": "queued"},
    )
    assert "heartbeating" in queued

    busy = _no_puller_error_message(
        step_key="writer",
        puller="p1",
        model="m1",
        attempts=3,
        puller_was_alive=True,
        task_status={"status": "fetched"},
    )
    assert "stayed busy" in busy

    offline = _no_puller_error_message(
        step_key="writer",
        puller="",
        model="m1",
        attempts=2,
    )
    assert "No puller picked up" in offline


def test_response_timeout_error_message_variants() -> None:
    fetched = _response_timeout_error_message(
        step_key="writer",
        puller="p1",
        puller_was_alive=True,
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

    heartbeat = _response_timeout_error_message(
        step_key="writer",
        puller="p1",
        puller_was_alive=True,
        task_status={"status": "unknown"},
    )
    assert "stopped heartbeating" in heartbeat

    generic = _response_timeout_error_message(
        step_key="writer",
        puller="",
        puller_was_alive=False,
        task_status={},
    )
    assert "timed out waiting" in generic


@pytest.mark.asyncio
async def test_execute_step_response_error_breaks(configured_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={})
    cp.get_task_status = AsyncMock(return_value={"status": "fetched"})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        return_value=[{"message": {"content": ""}, "error": "puller crashed", "usage": {}}]
    )
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
        )

    assert result["error"] == "puller crashed"


@pytest.mark.asyncio
async def test_execute_step_non_dict_usage(configured_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 1)

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={})
    cp.get_task_status = AsyncMock(return_value={"status": "completed"})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        return_value=[
            {
                "message": {"content": "ok"},
                "usage": ["not", "a", "dict"],
            }
        ]
    )
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
        )

    assert result["content"] == "ok"
    assert result["usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_execute_step_run_cancelled(configured_db, monkeypatch) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    monkeypatch.setattr(settings, "step_poll_interval_seconds", 0.01)

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-cancel-exec", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-cancel-exec", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={})
    cp.get_task_status = AsyncMock(return_value=None)
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.workers.executor.is_run_cancelled",
            AsyncMock(return_value=True),
        ):
            with pytest.raises(RunCancelledError):
                await execute_step(
                    cp,
                    step_key="writer",
                    system_prompt="sys",
                    user_content="user",
                    puller="p",
                    model="m",
                    run_id="run-cancel-exec",
                    tracer=tracer,
                )

    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_run_step_from_context_cancelled(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-ctx-cancel", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-ctx-cancel", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={})

    ctx = StepContext(
        step_key="writer",
        label="Writer",
        system_prompt="sys",
        user_prompt_template="{{topic}}",
        puller="p",
        model="m",
        variables={"topic": "Game"},
    )

    with patch(
        "article_factory.workers.executor.execute_step",
        AsyncMock(side_effect=RunCancelledError("stopped")),
    ):
        with pytest.raises(RunCancelledError):
            await run_step_from_context(ctx, cp, tracer=tracer)

    assert tracer.execution.status == "failed"
    assert tracer.execution.error == "Run stopped"
