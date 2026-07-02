from __future__ import annotations

import json
from typing import Any

from article_factory.services.tool_usage import aggregate_tool_use_by_step
from article_factory.services.iteration_stats import attach_iteration_metadata


def estimate_tokens_from_text(text: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return max(1, len(cleaned) // 4)


def serialize_tool_calls(tool_calls: Any) -> str:
    if not tool_calls:
        return ""
    try:
        return json.dumps(tool_calls, ensure_ascii=False)
    except TypeError:
        return str(tool_calls)


def serialize_tools_for_token_estimate(tool_defs: list[dict[str, Any]] | None) -> str:
    if not tool_defs:
        return ""
    return json.dumps(tool_defs, ensure_ascii=False)


def _message_content_parts(message: dict[str, Any]) -> str:
    parts: list[str] = []
    content = str(message.get("content") or "").strip()
    if content:
        parts.append(content)
    thinking = message.get("thinking")
    if thinking:
        parts.append(str(thinking))
    tool_calls = serialize_tool_calls(message.get("tool_calls"))
    if tool_calls:
        parts.append(tool_calls)
    return "\n".join(parts)


def serialize_messages_for_token_estimate(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "message")
        body = _message_content_parts(message)
        if body.strip():
            chunks.append(f"[{role}]\n{body}")
        else:
            chunks.append(f"[{role}]")
    return "\n\n".join(chunks)


def normalize_round_usage(
    usage: dict[str, Any] | None,
    *,
    messages: list[dict[str, Any]],
    assistant_message: dict[str, Any] | None = None,
    tools_text: str = "",
) -> dict[str, int]:
    """Normalize one LLM round, estimating from full message context when puller usage is missing."""
    assistant_message = assistant_message or {}
    input_text = serialize_messages_for_token_estimate(messages)
    if tools_text.strip():
        input_text = f"{input_text}\n\n[tools]\n{tools_text}".strip()
    output_text = _message_content_parts(assistant_message)

    data = dict(usage or {})
    input_tokens = int(data.get("input_tokens") or 0)
    output_tokens = int(data.get("output_tokens") or 0)
    total_tokens = int(data.get("total_tokens") or 0)

    estimated_input = estimate_tokens_from_text(input_text)
    estimated_output = estimate_tokens_from_text(output_text)

    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return normalize_usage(None, input_text=input_text, output_text=output_text)

    if input_tokens == 0 and estimated_input > 0:
        input_tokens = estimated_input
    if output_tokens == 0 and estimated_output > 0:
        output_tokens = estimated_output
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    elif input_tokens + output_tokens > total_tokens:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def normalize_usage(
    usage: dict[str, Any] | None,
    *,
    input_text: str = "",
    output_text: str = "",
) -> dict[str, int]:
    data = dict(usage or {})
    input_tokens = int(data.get("input_tokens") or 0)
    output_tokens = int(data.get("output_tokens") or 0)
    total_tokens = int(data.get("total_tokens") or 0)
    estimated = False

    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        estimated = True
        estimated_in = estimate_tokens_from_text(input_text)
        estimated_out = estimate_tokens_from_text(output_text)
        if estimated_in == 0 and estimated_out > 0:
            estimated_in = max(32, estimated_out // 3)
        input_tokens = estimated_in
        output_tokens = estimated_out
        total_tokens = input_tokens + output_tokens
    elif total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    if estimated and output_tokens > 0 and input_tokens < 8:
        input_tokens = max(32, output_tokens // 3)
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def finalize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    result = dict(stats)
    input_tokens = int(result.get("input_tokens") or 0)
    output_tokens = int(result.get("output_tokens") or 0)
    total_tokens = int(result.get("total_tokens") or 0)

    if total_tokens == 0 and (input_tokens > 0 or output_tokens > 0):
        total_tokens = input_tokens + output_tokens
    elif total_tokens > 0 and input_tokens == 0 and output_tokens == 0:
        output_tokens = max(1, (total_tokens * 3) // 4)
        input_tokens = max(1, total_tokens - output_tokens)
    elif total_tokens > 0 and input_tokens > 0 and output_tokens == 0:
        output_tokens = max(1, total_tokens - input_tokens)
    elif total_tokens > 0 and output_tokens > 0 and input_tokens == 0:
        input_tokens = max(1, total_tokens - output_tokens)
    elif input_tokens > 0 and output_tokens > 0:
        total_tokens = input_tokens + output_tokens

    result["input_tokens"] = input_tokens
    result["output_tokens"] = output_tokens
    result["total_tokens"] = total_tokens
    return result


def step_turns(step: dict[str, Any]) -> int:
    raw = step.get("turns")
    if raw is not None:
        return max(0, int(raw))
    tools = step.get("tools_used") if isinstance(step.get("tools_used"), list) else []
    if tools:
        rounds = [int(entry.get("round") or 1) for entry in tools if isinstance(entry, dict)]
        if rounds:
            return max(rounds)
    if step.get("content") or step.get("response_content") or step.get("usage"):
        return 1
    return 0


def aggregate_usage_stats(steps: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
        "total_turns": 0,
        "total_duration_ms": 0,
        "estimated_cost_usd": 0.0,
    }
    for step in steps:
        usage = step.get("usage") or {}
        turns = step_turns(step)
        totals["input_tokens"] += int(usage.get("input_tokens") or 0)
        totals["output_tokens"] += int(usage.get("output_tokens") or 0)
        totals["total_tokens"] += int(usage.get("total_tokens") or 0)
        totals["total_duration_ms"] += int(step.get("duration_ms") or 0)
        totals["total_turns"] += turns
        totals["llm_calls"] += turns
    return finalize_stats(totals)


def enrich_step_record(
    record: dict[str, Any],
    *,
    selected_model: str = "",
    body_markdown: str = "",
) -> dict[str, Any]:
    step = dict(record)
    content = str(step.get("content") or body_markdown or "")
    if selected_model and not str(step.get("model") or "").strip():
        step["model"] = selected_model
    step["usage"] = normalize_usage(
        step.get("usage") if isinstance(step.get("usage"), dict) else None,
        input_text=str(step.get("prompt") or step.get("step_key") or "prompt"),
        output_text=content,
    )
    if int(step["usage"].get("total_tokens") or 0) == 0:
        tools_used = step.get("tools_used") if isinstance(step.get("tools_used"), list) else []
        tool_text = "\n".join(
            str(entry.get("detail") or entry.get("tool") or "")
            for entry in tools_used
            if isinstance(entry, dict)
        )
        if tool_text.strip():
            step["usage"] = normalize_usage(
                step.get("usage") if isinstance(step.get("usage"), dict) else None,
                input_text=f"{step.get('prompt') or step.get('step_key') or 'prompt'}\n{tool_text}",
                output_text=content,
            )
    step["turns"] = step_turns(step)
    return step


def enrich_manifest(
    manifest: dict[str, Any] | None,
    *,
    selected_model: str = "",
    body_markdown: str = "",
) -> dict[str, Any]:
    data = dict(manifest or {})
    if selected_model and not str(data.get("selected_model") or "").strip():
        data["selected_model"] = selected_model

    raw_steps = data.get("step_stats") or data.get("steps") or []
    steps = [
        enrich_step_record(
            step if isinstance(step, dict) else {},
            selected_model=selected_model,
            body_markdown=body_markdown,
        )
        for step in raw_steps
    ]
    if not steps and body_markdown.strip():
        steps = [
            enrich_step_record(
                {
                    "step_key": "writer",
                    "step_name": "Article",
                    "content": body_markdown,
                    "model": selected_model,
                    "duration_ms": int((data.get("stats") or {}).get("total_duration_ms") or 0),
                },
                selected_model=selected_model,
                body_markdown=body_markdown,
            )
        ]

    data["steps"] = steps
    data["step_stats"] = steps
    data["stats"] = aggregate_usage_stats(steps)
    data["tool_use"] = aggregate_tool_use_by_step(steps)
    return attach_iteration_metadata(
        data,
        draft_number=int(data.get("draft_number") or 0),
        review_round=int(data.get("review_round") or 0),
    )
