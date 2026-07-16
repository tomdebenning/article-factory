from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from article_factory.services.control_plane_completion import (
    extract_json_object,
    run_control_plane_completion,
)


@pytest.mark.asyncio
async def test_run_control_plane_completion_returns_content() -> None:
    cp = MagicMock()
    item = {"message": {"content": "Improved prompt text"}}

    with patch(
        "article_factory.services.control_plane_completion._submit_and_wait_for_round",
        new=AsyncMock(return_value=(item, None, "conv-1")),
    ):
        content = await run_control_plane_completion(
            cp=cp,
            puller="gpu-01",
            model="llama3",
            messages=[{"role": "user", "content": "Improve this"}],
        )
    assert content == "Improved prompt text"


@pytest.mark.asyncio
async def test_run_control_plane_completion_no_item() -> None:
    with patch(
        "article_factory.services.control_plane_completion._submit_and_wait_for_round",
        new=AsyncMock(return_value=(None, None, "conv-1")),
    ):
        with pytest.raises(RuntimeError, match="did not return"):
            await run_control_plane_completion(
                cp=MagicMock(),
                puller="gpu-01",
                model="llama3",
                messages=[{"role": "user", "content": "x"}],
            )


@pytest.mark.asyncio
async def test_run_control_plane_completion_empty_content() -> None:
    with patch(
        "article_factory.services.control_plane_completion._submit_and_wait_for_round",
        new=AsyncMock(return_value=({"message": {"content": "  "}}, None, "conv-1")),
    ):
        with pytest.raises(RuntimeError, match="empty content"):
            await run_control_plane_completion(
                cp=MagicMock(),
                puller="gpu-01",
                model="llama3",
                messages=[{"role": "user", "content": "x"}],
            )


def test_extract_json_object_from_fence() -> None:
    text = 'Here is the plan:\n```json\n{"step_key": "writer", "prompt": "new"}\n```'
    payload = extract_json_object(text)
    assert payload["step_key"] == "writer"


def test_extract_json_object_from_bare_object() -> None:
    text = 'Answer: {"ok": true, "count": 2} trailing'
    assert extract_json_object(text) == {"ok": True, "count": 2}


def test_extract_json_object_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        extract_json_object(json.dumps(["list"]))
