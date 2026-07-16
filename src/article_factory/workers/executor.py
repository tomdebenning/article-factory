from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from article_factory.config import settings
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.services.run_control import RunCancelledError, is_run_cancelled
from article_factory.services.step_tools import (
    MAX_TOOL_ROUNDS,
    StepToolRegistry,
    _parse_tool_arguments,
    augment_system_prompt_for_tools,
    build_step_tool_definitions,
    looks_like_tool_refusal,
    resolve_step_tools,
    run_workspace_root,
    step_has_tools,
    tool_use_nudge_message,
)
from article_factory.services.puller_selection import get_registered_puller_on_cp
from article_factory.services.step_trace import StepTracer, duration_ms_between
from article_factory.services.token_usage import (
    normalize_round_usage,
    normalize_usage,
    serialize_tools_for_token_estimate,
)
from article_factory.services.tool_usage import aggregate_tool_use_by_step, tool_use_entry
from article_factory.workers.base import StepContext, render_prompt, prepend_current_datetime

logger = logging.getLogger(__name__)

WORKER_AGENT_PREFIX = "factory-worker"

PollOutcome = str  # "response" | "no_puller" | "timeout"


class StepDispatchError(RuntimeError):
    """Step could not be dispatched to a puller for LLM processing."""


class NoPullerAvailableError(StepDispatchError):
    """No puller fetched the task after repeated resubmits."""


ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


def worker_agent_id(step_key: str) -> str:
    return f"{WORKER_AGENT_PREFIX}-{step_key}"


def _merge_usage(totals: dict[str, int], usage: dict[str, int]) -> dict[str, int]:
    return {
        "input_tokens": totals["input_tokens"] + int(usage.get("input_tokens") or 0),
        "output_tokens": totals["output_tokens"] + int(usage.get("output_tokens") or 0),
        "total_tokens": totals["total_tokens"] + int(usage.get("total_tokens") or 0),
    }


def _assistant_message_dict(message: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": str(message.get("content") or ""),
    }
    tool_calls = message.get("tool_calls")
    if tool_calls:
        payload["tool_calls"] = tool_calls
    thinking = message.get("thinking")
    if thinking:
        payload["thinking"] = thinking
    return payload


async def _poll_step_response(
    cp: ControlPlaneClient,
    *,
    agent_id: str,
    conversation_id: str,
    round_num: int,
    run_id: str | None,
    tracer: StepTracer | None,
    pulled_seen: bool,
    target_puller: str = "",
    no_puller_timeout: float | None = None,
) -> tuple[dict[str, Any] | None, bool, PollOutcome, bool, dict[str, Any] | None]:
    poll_interval = settings.step_poll_interval_seconds
    no_puller_timeout = no_puller_timeout or settings.step_no_puller_timeout_seconds
    response_timeout = settings.step_response_timeout_seconds
    max_no_puller_polls = max(1, int(no_puller_timeout / poll_interval))
    max_response_polls = max(1, int(response_timeout / poll_interval))
    alive_check_interval = settings.step_puller_alive_check_interval_seconds
    task_status_interval = settings.step_task_status_check_interval_seconds
    stale_grace = settings.step_puller_stale_grace_seconds
    absolute_max_wait = settings.step_busy_puller_max_wait_seconds

    pulled = pulled_seen
    submitted_at = time.monotonic()
    fetched_at: float | None = None
    no_puller_polls = 0
    response_polls = 0
    waiting_marked = False
    puller_alive = False
    puller_was_alive = False
    last_puller_check_at = 0.0
    last_puller_alive_at = 0.0
    last_task_status_check_at = 0.0
    task_status: dict[str, Any] | None = None

    while True:
        if run_id and await is_run_cancelled(run_id):
            if tracer:
                tracer.mark_failed("Run stopped")
            raise RunCancelledError(f"Run {run_id} was stopped")

        now = time.monotonic()
        total_elapsed = now - submitted_at

        if (
            last_task_status_check_at == 0.0
            or (now - last_task_status_check_at) >= task_status_interval
        ):
            task_status = await cp.get_task_status(conversation_id)
            last_task_status_check_at = now
            if task_status and tracer:
                tracer.record_task_status(task_status)

        if target_puller and (
            last_puller_check_at == 0.0 or (now - last_puller_check_at) >= alive_check_interval
        ):
            puller_record = await get_registered_puller_on_cp(cp, target_puller)
            puller_alive = puller_record is not None
            last_puller_check_at = now
            if puller_alive:
                puller_was_alive = True
                last_puller_alive_at = now

        cp_status = str((task_status or {}).get("status") or "")
        if not pulled and cp_status in {"fetched", "completed", "failed"}:
            pulled = True
            fetched_at = now
            if tracer:
                tracer.mark_pulled()

        if absolute_max_wait > 0 and total_elapsed >= absolute_max_wait:
            if pulled:
                return None, True, "timeout", puller_was_alive, task_status
            return None, False, "no_puller", puller_was_alive, task_status

        if not pulled:
            no_puller_polls += 1
            if puller_alive or cp_status == "queued":
                if tracer and not waiting_marked:
                    if cp_status == "queued":
                        tracer.record_activity("Task queued on puller — waiting for fetch")
                    else:
                        tracer.record_activity("Waiting for puller to finish its current task")
                    tracer.mark_waiting()
                    waiting_marked = True
            elif puller_was_alive:
                if (now - last_puller_alive_at) >= stale_grace:
                    return None, False, "no_puller", puller_was_alive, task_status
                if tracer and not waiting_marked:
                    tracer.record_activity("Waiting for puller to reconnect")
                    tracer.mark_waiting()
                    waiting_marked = True
            elif (now - submitted_at) >= no_puller_timeout or no_puller_polls > max_no_puller_polls:
                return None, False, "no_puller", puller_was_alive, task_status

            if not pulled and await cp.task_was_fetched(conversation_id=conversation_id):
                pulled = True
                fetched_at = now
                if tracer:
                    tracer.mark_pulled()
            elif tracer and not waiting_marked and (now - submitted_at) >= min(10.0, no_puller_timeout / 3):
                tracer.mark_waiting()
                waiting_marked = True
        else:
            response_polls += 1
            elapsed = (now - fetched_at) if fetched_at is not None else 0.0
            if puller_alive or cp_status in {"fetched", "completed", "failed"}:
                if tracer and response_polls == 1:
                    tracer.record_activity("Waiting for puller response", cp_round=round_num)
            elif puller_was_alive:
                if (now - last_puller_alive_at) >= stale_grace:
                    return None, True, "timeout", puller_was_alive, task_status
            elif elapsed >= response_timeout or response_polls > max_response_polls:
                return None, True, "timeout", puller_was_alive, task_status
            elif tracer and response_polls == 1:
                tracer.record_activity("Waiting for puller response", cp_round=round_num)

        responses = await cp.poll_responses(
            agent_id,
            conversation_id=conversation_id,
            round_num=round_num,
        )
        if responses:
            return responses[-1], pulled, "response", puller_was_alive, task_status
        await asyncio.sleep(poll_interval)


def _task_status_context(task_status: dict[str, Any] | None) -> str:
    if not isinstance(task_status, dict):
        return ""
    status = str(task_status.get("status") or "unknown")
    parts = [f"control-plane status={status}"]
    if task_status.get("queue_depth_at_submit") is not None:
        parts.append(f"queue depth at submit={task_status['queue_depth_at_submit']}")
    if task_status.get("fetched_by"):
        parts.append(f"fetched by={task_status['fetched_by']}")
    if task_status.get("fetched_at"):
        parts.append(f"fetched at={task_status['fetched_at']}")
    if task_status.get("response_error"):
        err = str(task_status["response_error"])
        parts.append(f"puller error={err[:240]}")
    return " (" + "; ".join(parts) + ")"


def _no_puller_error_message(
    *,
    step_key: str,
    puller: str,
    model: str,
    attempts: int,
    puller_was_alive: bool = False,
    waited_seconds: int | None = None,
    task_status: dict[str, Any] | None = None,
) -> str:
    context = _task_status_context(task_status)
    cp_status = str((task_status or {}).get("status") or "")
    if cp_status == "queued" and puller_was_alive and puller:
        wait_s = waited_seconds or int(settings.step_busy_puller_max_wait_seconds)
        return (
            f"Puller “{puller}” is heartbeating but has not fetched the {step_key} task yet "
            f"after ~{wait_s}s. The task is still queued on the control plane{context}."
        )
    if puller_was_alive and puller:
        wait_s = waited_seconds or int(settings.step_busy_puller_max_wait_seconds)
        return (
            f"Puller “{puller}” stayed busy on the {step_key} step and did not pick up the task "
            f"within ~{wait_s}s. It was still heartbeating on the control plane — the puller queue "
            f"may be backed up{context}."
        )
    wait_s = waited_seconds or int(settings.step_no_puller_timeout_seconds)
    return (
        f"No puller picked up the {step_key} step after {attempts} attempts "
        f"(waited ~{wait_s}s each). "
        f"Ensure a puller is running for model “{model}”"
        f"{f' (target puller: {puller})' if puller else ''}{context}."
    )


def _response_timeout_error_message(
    *,
    step_key: str,
    puller: str,
    puller_was_alive: bool,
    waited_seconds: int | None = None,
    task_status: dict[str, Any] | None = None,
) -> str:
    context = _task_status_context(task_status)
    cp_status = str((task_status or {}).get("status") or "")
    wait_s = waited_seconds or int(settings.step_busy_puller_max_wait_seconds)
    if cp_status == "fetched":
        return (
            f"Step {step_key} timed out after ~{wait_s}s. Puller “{puller or task_status.get('fetched_by') or '?'}” "
            f"fetched the task but no response arrived on the control plane{context}."
        )
    if cp_status == "failed":
        return (
            f"Step {step_key} failed on puller “{puller or task_status.get('fetched_by') or '?'}”"
            f"{context}."
        )
    if puller_was_alive and puller:
        return (
            f"Step {step_key} timed out after ~{wait_s}s waiting for a response from puller “{puller}”. "
            f"The puller stopped heartbeating or exceeded the busy wait limit{context}."
        )
    wait_s = waited_seconds or int(settings.step_response_timeout_seconds)
    return (
        f"Step {step_key} timed out waiting for control plane response after a puller fetched it (~{wait_s}s)"
        f"{context}."
    )


async def _submit_and_wait_for_round(
    cp: ControlPlaneClient,
    *,
    step_key: str,
    puller: str,
    model: str,
    build_task: Callable[[str, str], dict[str, Any]],
    round_num: int,
    run_id: str | None,
    tracer: StepTracer | None,
) -> tuple[dict[str, Any], str, str]:
    max_attempts = max(1, settings.step_no_puller_max_attempts)
    last_outcome: PollOutcome = "no_puller"
    puller_was_alive = False
    last_task_status: dict[str, Any] | None = None

    for attempt in range(1, max_attempts + 1):
        conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
        agent_id = worker_agent_id(step_key)
        task = build_task(agent_id, conversation_id)

        submit_result = await cp.submit_task(task)
        if tracer:
            if round_num == 1 and attempt == 1:
                tracer.mark_submitted(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    queue_depth=submit_result.get("queue_depth"),
                    cp_round=round_num,
                )
            else:
                tracer.record_cp_round(
                    cp_round=round_num,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    queue_depth=submit_result.get("queue_depth"),
                )

        item, _pulled, outcome, attempt_puller_alive, task_status = await _poll_step_response(
            cp,
            agent_id=agent_id,
            conversation_id=conversation_id,
            round_num=round_num,
            run_id=run_id,
            tracer=tracer,
            pulled_seen=False,
            target_puller=puller,
        )
        last_outcome = outcome
        puller_was_alive = puller_was_alive or attempt_puller_alive
        if task_status:
            last_task_status = task_status

        if outcome == "response" and item is not None:
            return item, agent_id, conversation_id

        if outcome == "timeout":
            break

        if attempt_puller_alive:
            logger.warning(
                "Puller %s stayed busy for %s step — not resubmitting while it heartbeats on the control plane",
                puller,
                step_key,
            )
            break

        if attempt < max_attempts:
            logger.warning(
                "No puller fetched %s step (attempt %s/%s, puller=%s, model=%s) — resubmitting",
                step_key,
                attempt,
                max_attempts,
                puller,
                model,
            )

    if last_outcome == "timeout":
        message = _response_timeout_error_message(
            step_key=step_key,
            puller=puller,
            puller_was_alive=puller_was_alive,
            waited_seconds=int(settings.step_busy_puller_max_wait_seconds if puller_was_alive else settings.step_response_timeout_seconds),
            task_status=last_task_status,
        )
        if tracer:
            tracer.mark_failed(message)
        raise TimeoutError(message)

    message = _no_puller_error_message(
        step_key=step_key,
        puller=puller,
        model=model,
        attempts=max_attempts,
        puller_was_alive=puller_was_alive,
        waited_seconds=int(
            settings.step_busy_puller_max_wait_seconds if puller_was_alive else settings.step_no_puller_timeout_seconds
        ),
        task_status=last_task_status,
    )
    if tracer:
        tracer.mark_failed(message)
    raise NoPullerAvailableError(message)


def _empty_response_retry_message(step_key: str) -> str:
    suffix = ""
    if step_key in {"review", "step_2"}:
        suffix = " End with a final line: VERDICT: ACCEPT or VERDICT: REJECT."
    return (
        f"Your previous attempt for the {step_key} step returned no usable text. "
        f"Reply again with your complete answer in plain text.{suffix}"
    )


async def execute_step(
    cp: ControlPlaneClient,
    *,
    step_key: str,
    system_prompt: str,
    user_content: str,
    puller: str,
    model: str,
    run_id: str | None = None,
    tracer: StepTracer | None = None,
    enabled_tools: dict[str, bool] | None = None,
    brave_search_api_key: str = "",
) -> dict:
    if not puller or not model:
        raise RuntimeError(f"Step {step_key} missing puller/model configuration")

    tool_flags = resolve_step_tools(enabled_tools)
    tool_defs = build_step_tool_definitions(tool_flags)
    registry: StepToolRegistry | None = None
    if tool_defs and run_id:
        registry = StepToolRegistry(
            workspace_root=run_workspace_root(run_id),
            brave_api_key=brave_search_api_key,
        )

    initial_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": augment_system_prompt_for_tools(
                prepend_current_datetime(system_prompt),
                tool_flags,
            ),
        },
        {"role": "user", "content": user_content},
    ]
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    final_content = ""
    completed_at: str | None = None
    response_error: str | None = None
    item: dict[str, Any] | None = None
    tools_used: list[dict[str, Any]] = []
    agent_id = ""
    conversation_id = ""
    total_turn_count = 0
    max_empty_attempts = max(1, settings.step_empty_response_max_attempts)

    for empty_attempt in range(1, max_empty_attempts + 1):
        messages = [dict(message) for message in initial_messages]
        if empty_attempt > 1:
            messages.append({"role": "user", "content": _empty_response_retry_message(step_key)})
            logger.warning(
                "Step %s returned empty content — retrying attempt %s/%s",
                step_key,
                empty_attempt,
                max_empty_attempts,
            )
            if tracer:
                tracer.record_activity(
                    f"Retrying after empty response ({empty_attempt}/{max_empty_attempts})",
                )

        round_num = 1
        attempt_turn_count = 0
        tool_refusal_nudged = False
        final_content = ""
        response_error = None

        def build_task(task_agent_id: str, task_conversation_id: str) -> dict[str, Any]:
            task: dict[str, Any] = {
                "agent_id": task_agent_id,
                "conversation_id": task_conversation_id,
                "round": round_num,
                "target_puller": puller or settings.default_puller,
                "model": model or settings.default_model,
                "messages": messages,
                "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            if tool_defs:
                task["tools"] = tool_defs
            return task

        while round_num <= MAX_TOOL_ROUNDS:
            attempt_turn_count += 1

            item, agent_id, conversation_id = await _submit_and_wait_for_round(
                cp,
                step_key=step_key,
                puller=puller,
                model=model,
                build_task=build_task,
                round_num=round_num,
                run_id=run_id,
                tracer=tracer,
            )

            message = (item.get("message") or {}) if isinstance(item.get("message"), dict) else {}
            content = str(message.get("content") or "")
            response_error = item.get("error")
            if response_error:
                break

            usage = item.get("usage")
            if usage is not None and not isinstance(usage, dict):
                usage = dict(usage) if hasattr(usage, "items") else None
            normalized_usage = normalize_round_usage(
                usage,
                messages=messages,
                assistant_message=message,
                tools_text=serialize_tools_for_token_estimate(tool_defs) if tool_defs else "",
            )
            total_usage = _merge_usage(total_usage, normalized_usage)
            completed_at = item.get("completed_at")

            tool_calls = message.get("tool_calls")
            if not tool_calls or not registry:
                if (
                    registry
                    and not tool_refusal_nudged
                    and step_has_tools(tool_flags)
                    and looks_like_tool_refusal(content)
                ):
                    messages.append(_assistant_message_dict(message))
                    messages.append({"role": "user", "content": tool_use_nudge_message(tool_flags)})
                    tool_refusal_nudged = True
                    round_num += 1
                    continue
                final_content = content
                break

            messages.append(_assistant_message_dict(message))
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                tool_name = str(fn.get("name") or "tool")
                tool_args = _parse_tool_arguments(fn.get("arguments"))
                if tracer:
                    tracer.record_tool_start(tool_name, tool_args, round_num=round_num)
                tool_message = await registry.execute(tool_call)
                entry = tool_use_entry(
                    tool_name,
                    tool_args,
                    result=str(tool_message.get("content") or ""),
                    round_num=round_num,
                )
                if tracer:
                    tracer.append_tool_use(entry)
                else:
                    tools_used.append(entry)
                messages.append(tool_message)
            round_num += 1
            final_content = content

        total_turn_count += attempt_turn_count

        if response_error:
            break
        if str(final_content or "").strip():
            break

    if item is None and not response_error:
        message = f"Step {step_key} ended without a control plane response"
        if tracer:
            tracer.mark_failed(message)
        raise RuntimeError(message)

    if not response_error and not str(final_content or "").strip():
        message = (
            f"Step {step_key} returned empty content after {total_turn_count} turn(s) "
            f"across {max_empty_attempts} attempt(s). "
            "The model may have used tools without producing a final answer."
        )
        if tracer:
            tracer.mark_failed(message)
        raise RuntimeError(message)

    if tracer:
        if response_error:
            tracer.mark_failed(str(response_error))
        else:
            tracer.mark_completed(
                response_content=final_content,
                usage=total_usage,
                duration_ms=duration_ms_between(tracer.execution.started_at),
                tools_used=tools_used,
                turns=total_turn_count,
            )

    return {
        "content": final_content,
        "usage": total_usage,
        "completed_at": completed_at,
        "error": response_error,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "tools_used": tools_used,
        "turns": total_turn_count,
    }


async def run_step_from_context(
    ctx: StepContext,
    cp: ControlPlaneClient | None = None,
    *,
    run_id: str | None = None,
    tracer: StepTracer | None = None,
) -> dict:
    client = cp or ControlPlaneClient()
    user_content = render_prompt(ctx.user_prompt_template, ctx.variables)
    started = datetime.now(timezone.utc)
    effective_run_id = run_id or ctx.run_id or None
    try:
        result = await execute_step(
            client,
            step_key=ctx.step_key,
            system_prompt=ctx.system_prompt,
            user_content=user_content,
            puller=ctx.puller,
            model=ctx.model,
            run_id=effective_run_id,
            tracer=tracer,
            enabled_tools=ctx.enabled_tools,
            brave_search_api_key=ctx.brave_search_api_key,
        )
    except RunCancelledError:
        if tracer:
            tracer.mark_failed("Run stopped")
        raise
    except Exception as exc:
        if tracer:
            tracer.mark_failed(str(exc) or type(exc).__name__)
        raise
    finished = datetime.now(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)
    usage = normalize_usage(
        result.get("usage") if isinstance(result.get("usage"), dict) else None,
        input_text=user_content,
        output_text=str(result.get("content") or ""),
    )
    return {
        "step_key": ctx.step_key,
        "step_name": ctx.label,
        "content": result.get("content") or "",
        "error": result.get("error"),
        "duration_ms": duration_ms,
        "usage": usage,
        "model": ctx.model,
        "completed_at": finished.isoformat(),
        "agent_id": result.get("agent_id"),
        "conversation_id": result.get("conversation_id"),
        "tools_used": result.get("tools_used") if isinstance(result.get("tools_used"), list) else [],
        "turns": int(result.get("turns") or 0),
    }
