"""One-shot control-plane LLM completion for analysis tasks."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.workers.executor import _submit_and_wait_for_round

logger = logging.getLogger(__name__)

PROMPT_IMPROVER_AGENT_ID = "factory-prompt-improver"


async def run_control_plane_completion(
    *,
    cp: ControlPlaneClient,
    puller: str,
    model: str,
    messages: list[dict[str, str]],
    agent_id: str = PROMPT_IMPROVER_AGENT_ID,
) -> str:
    def build_task(task_agent_id: str, task_conversation_id: str) -> dict[str, Any]:
        return {
            "agent_id": task_agent_id,
            "conversation_id": task_conversation_id,
            "round": 1,
            "target_puller": puller,
            "model": model,
            "messages": messages,
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    item, _, _conversation_id = await _submit_and_wait_for_round(
        cp,
        step_key="prompt-improver",
        puller=puller,
        model=model,
        build_task=build_task,
        round_num=1,
        run_id=None,
        tracer=None,
    )
    if item is None:
        raise RuntimeError("Prompt improvement LLM call did not return a response")

    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("Prompt improvement LLM returned empty content")
    return content


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must be an object")
    return payload
