from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.workers.base import StepContext, prepend_current_datetime
from article_factory.workers.executor import NoPullerAvailableError, execute_step, run_step_from_context, worker_agent_id


def _mock_control_plane(**overrides: object) -> AsyncMock:
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value=None)
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])
    for key, value in overrides.items():
        setattr(cp, key, value)
    return cp


def test_prepend_current_datetime() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 5, 21, 15, 30, 0, tzinfo=timezone.utc)
    assert prepend_current_datetime("You are a writer.", now=now) == (
        "Current date and time: 2026-05-21 15:30:00 UTC\n\nYou are a writer."
    )
    assert prepend_current_datetime("  ", now=now) == "Current date and time: 2026-05-21 15:30:00 UTC"


def test_worker_agent_id() -> None:
    assert worker_agent_id("writer") == "factory-worker-writer"


@pytest.mark.asyncio
async def test_execute_step_success() -> None:
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value=None)
    cp.submit_task = AsyncMock(return_value={"ok": True})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(
        return_value=[
            {
                "message": {"content": "Draft body"},
                "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
                "completed_at": "2026-01-01T00:00:00Z",
            }
        ]
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="puller-a",
            model="model-a",
        )

    assert result["content"] == "Draft body"
    assert result["usage"]["total_tokens"] == 15
    system_message = cp.submit_task.await_args.args[0]["messages"][0]["content"]
    assert system_message.startswith("Current date and time:")
    assert "sys" in system_message


@pytest.mark.asyncio
async def test_execute_step_missing_puller() -> None:
    cp = AsyncMock(spec=ControlPlaneClient)
    with pytest.raises(RuntimeError, match="missing puller/model"):
        await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="",
            model="",
        )


@pytest.mark.asyncio
async def test_execute_step_timeout(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 3.0)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 3)

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value=None)
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(NoPullerAvailableError, match="No puller picked up"):
            await execute_step(
                cp,
                step_key="writer",
                system_prompt="sys",
                user_content="user",
                puller="p",
                model="m",
            )

    assert cp.submit_task.await_count == 3


@pytest.mark.asyncio
async def test_run_step_from_context() -> None:
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value=None)
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(
        return_value=[{"message": {"content": "ok"}, "usage": {}}]
    )

    ctx = StepContext(
        step_key="writer",
        label="Writer",
        system_prompt="sys",
        user_prompt_template="Topic: {{topic}}",
        puller="p",
        model="m",
        variables={"topic": "Game"},
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        record = await run_step_from_context(ctx, cp)

    assert record["step_key"] == "writer"
    assert record["content"] == "ok"
    assert record["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_execute_step_with_tracer(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-exec", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-exec", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 3})
    cp.task_was_fetched = AsyncMock(return_value=True)
    poll_calls = {"n": 0}

    async def poll_side_effect(*args, **kwargs):
        poll_calls["n"] += 1
        if poll_calls["n"] >= 4:
            return [{"message": {"content": "done"}, "usage": {}}]
        return []

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="puller-a",
            model="model-a",
            tracer=tracer,
        )

    assert result["content"] == "done"
    assert tracer.execution.status == "completed"
    assert tracer.execution.cp_queue_depth == 3


@pytest.mark.asyncio
async def test_execute_step_timeout_marks_tracer_failed(configured_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 3.0)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 3)

    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-timeout", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-timeout", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])

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
    assert "No puller picked up" in (tracer.execution.error or "")


@pytest.mark.asyncio
async def test_execute_step_waits_longer_for_registered_busy_puller(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 3.0)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 12.0)
    monkeypatch.setattr(settings, "step_puller_alive_check_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 3)

    busy_puller = {
        "puller_name": "puller-felix-ollama",
        "is_active": True,
        "is_stale": False,
        "status": "busy",
        "supported_models": ["m"],
    }

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    cp.get_puller = AsyncMock(return_value=busy_puller)
    cp.list_pullers = AsyncMock(return_value=[busy_puller])
    cp.get_task_status = AsyncMock(return_value={"status": "queued", "queue_depth_at_submit": 1})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        call_count = {"n": 0}

        def mono() -> float:
            call_count["n"] += 1
            return 0.0 if call_count["n"] == 1 else 13.0

        with patch("article_factory.workers.executor.time.monotonic", side_effect=mono):
            with pytest.raises(NoPullerAvailableError, match="heartbeating but has not fetched"):
                await execute_step(
                    cp,
                    step_key="writer",
                    system_prompt="sys",
                    user_content="user",
                    puller="puller-felix-ollama",
                    model="m",
                )

    assert cp.submit_task.await_count == 1


@pytest.mark.asyncio
async def test_execute_step_resubmits_when_no_puller_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_no_puller_timeout_seconds", 3.0)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "step_no_puller_max_attempts", 3)

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 2})
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])
    submit_calls = {"n": 0}

    async def fetched_side_effect(*, conversation_id: str) -> bool:
        return submit_calls["n"] >= 2

    async def poll_side_effect(*args, **kwargs):
        if submit_calls["n"] >= 2:
            return [{"message": {"content": "done"}, "usage": {}}]
        return []

    cp.task_was_fetched = AsyncMock(side_effect=fetched_side_effect)

    async def track_submit(task):
        submit_calls["n"] += 1
        return {"queue_depth": 2}

    cp.submit_task = AsyncMock(side_effect=track_submit)
    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="puller-a",
            model="model-a",
        )

    assert result["content"] == "done"
    assert cp.submit_task.await_count == 2


@pytest.mark.asyncio
async def test_execute_step_extends_response_wait_while_puller_heartbeats(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_response_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "step_busy_puller_max_wait_seconds", 20.0)
    monkeypatch.setattr(settings, "step_puller_alive_check_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "step_poll_interval_seconds", 1.0)

    busy_puller = {
        "puller_name": "puller-felix-ollama",
        "is_active": True,
        "is_stale": False,
        "status": "busy",
        "supported_models": ["m"],
    }

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    cp.get_puller = AsyncMock(return_value=busy_puller)
    cp.list_pullers = AsyncMock(return_value=[busy_puller])
    cp.get_task_status = AsyncMock(return_value={"status": "fetched", "fetched_by": "puller-felix-ollama"})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(return_value=[])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        times = iter([0.0, 6.0, 21.0])

        with patch("article_factory.workers.executor.time.monotonic", side_effect=lambda: next(times)):
            with pytest.raises(TimeoutError, match="timed out after"):
                await execute_step(
                    cp,
                    step_key="writer",
                    system_prompt="sys",
                    user_content="user",
                    puller="puller-felix-ollama",
                    model="m",
                )

    assert cp.submit_task.await_count == 1


@pytest.mark.asyncio
async def test_execute_step_marks_waiting_before_pull(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-waiting", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-waiting", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    cp.get_puller = AsyncMock(return_value=None)
    cp.list_pullers = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)
    poll_calls = {"n": 0}

    async def poll_side_effect(*args, **kwargs):
        poll_calls["n"] += 1
        if poll_calls["n"] >= 6:
            return [{"message": {"content": "done"}, "usage": {}}]
        return []

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
            tracer=tracer,
        )

    assert tracer.execution.status == "completed"
    assert tracer.execution.submitted_at is not None


@pytest.mark.asyncio
async def test_run_step_from_context_failure_marks_tracer(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-fail", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-fail", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(side_effect=RuntimeError("cp down"))

    ctx = StepContext(
        step_key="writer",
        label="Writer",
        system_prompt="sys",
        user_prompt_template="{{topic}}",
        puller="p",
        model="m",
        variables={"topic": "Game"},
    )

    with pytest.raises(RuntimeError, match="cp down"):
        await run_step_from_context(ctx, cp, tracer=tracer)

    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_execute_step_retries_after_empty_response(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_empty_response_max_attempts", 3)

    responses = [
        {"message": {"content": ""}, "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1}},
        {
            "message": {"content": "Review complete.\n\nVERDICT: REJECT"},
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            "completed_at": "2026-01-01T00:00:00Z",
        },
    ]

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value=None)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 0})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(side_effect=[[response] for response in responses])

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="step_2",
            system_prompt="Review the draft.",
            user_content="Draft:\nArticle body",
            puller="puller-a",
            model="model-a",
        )

    assert result["content"] == "Review complete.\n\nVERDICT: REJECT"
    assert cp.submit_task.await_count == 2


@pytest.mark.asyncio
async def test_execute_step_fails_after_empty_response_retries_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_empty_response_max_attempts", 2)

    cp = AsyncMock(spec=ControlPlaneClient)
    cp.get_task_status = AsyncMock(return_value=None)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 0})
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.poll_responses = AsyncMock(
        return_value=[{"message": {"content": ""}, "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1}}]
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError, match="returned empty content"):
            await execute_step(
                cp,
                step_key="step_2",
                system_prompt="Review the draft.",
                user_content="Draft:\nArticle body",
                puller="puller-a",
                model="model-a",
            )

    assert cp.submit_task.await_count == 2


@pytest.mark.asyncio
async def test_execute_step_response_error(configured_db, monkeypatch) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    cp = AsyncMock()
    cp.get_task_status = AsyncMock(return_value={"status": "failed"})
    cp.submit_task = AsyncMock(return_value={})
    cp.task_was_fetched = AsyncMock(return_value=True)
    cp.poll_responses = AsyncMock(
        return_value=[{"message": {"content": "oops"}, "error": "model error", "usage": {}}]
    )

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="err-run", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="err-run", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="user",
            puller="p",
            model="m",
            tracer=tracer,
        )

    assert result["error"] == "model error"
    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_execute_step_tool_round_without_tracer(monkeypatch, tmp_path) -> None:
    import json

    monkeypatch.setattr("article_factory.config.settings.flow_run_outputs_root", str(tmp_path))

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
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({"path": "out.txt", "content": "saved"}),
                                },
                            },
                            "bad-call",
                        ],
                    },
                    "usage": {},
                }
            ],
            [{"message": {"content": "final answer"}, "usage": {}}],
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
            run_id="tool-round",
            enabled_tools={"write_file": True, "read_file": False, "web_search": False, "web_fetch": False},
        )

    assert result["content"] == "final answer"
    assert result["tools_used"]


@pytest.mark.asyncio
async def test_run_step_from_context_cancelled(configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun
    from article_factory.services.run_control import RunCancelledError, request_run_cancel
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="ctx-cancel", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="ctx-cancel", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    await request_run_cancel("ctx-cancel")

    cp = AsyncMock()
    cp.submit_task = AsyncMock(return_value={})
    cp.poll_responses = AsyncMock(return_value=[])
    cp.task_was_fetched = AsyncMock(return_value=False)
    cp.get_task_status = AsyncMock(return_value=None)

    ctx = StepContext(
        step_key="writer",
        label="Writer",
        system_prompt="sys",
        user_prompt_template="hi",
        puller="p",
        model="m",
        variables={},
        run_id="ctx-cancel",
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RunCancelledError):
            await run_step_from_context(ctx, cp, tracer=tracer)

    assert tracer.execution.status == "failed"


@pytest.mark.asyncio
async def test_run_step_from_context_generic_failure(configured_db) -> None:
    import article_factory.db as db_module
    from article_factory.models import FactoryRun
    from article_factory.services.step_trace import StepTracer

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="ctx-fail", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="ctx-fail", step_key="writer", puller="p", model="m")
    finally:
        db.close()

    cp = AsyncMock()
    cp.submit_task = AsyncMock(side_effect=RuntimeError("cp exploded"))

    ctx = StepContext(
        step_key="writer",
        label="Writer",
        system_prompt="sys",
        user_prompt_template="hi",
        puller="p",
        model="m",
        variables={},
    )

    with pytest.raises(RuntimeError, match="cp exploded"):
        await run_step_from_context(ctx, cp, tracer=tracer)

    assert tracer.execution.status == "failed"
