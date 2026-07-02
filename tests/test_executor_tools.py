from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.workers.executor import execute_step


@pytest.mark.asyncio
async def test_execute_step_runs_tool_loop(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    rounds = {"n": 0}

    async def poll_side_effect(agent_id, *, conversation_id, round_num, max_items=10):
        rounds["n"] += 1
        if round_num == 1:
            return [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"path":"draft.md","content":"Draft text"}',
                                },
                            }
                        ],
                    },
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                }
            ]
        return [
            {
                "message": {"content": "Draft saved."},
                "usage": {"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
            }
        ]

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        result = await execute_step(
            cp,
            step_key="writer",
            system_prompt="sys",
            user_content="Write a draft",
            puller="puller-a",
            model="model-a",
            run_id="run-tools",
            enabled_tools={"write_file": True, "web_search": False},
        )

    assert result["content"] == "Draft saved."
    assert result["usage"]["total_tokens"] == 35
    assert cp.submit_task.await_count == 2
    first_task = cp.submit_task.await_args_list[0].args[0]
    assert first_task.get("tools")
    workspace_file = tmp_path / "run-tools" / "workspace" / "draft.md"
    assert workspace_file.read_text(encoding="utf-8") == "Draft text"
    tools_used = result.get("tools_used") or []
    assert len(tools_used) == 1
    assert tools_used[0]["tool"] == "write_file"
    assert tools_used[0]["label"] == "Write file"
    assert tools_used[0]["detail"] == "draft.md"
    assert result.get("turns") == 2


@pytest.mark.asyncio
async def test_execute_step_estimates_tokens_for_tool_round_without_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    large_tool_result = "URL: https://example.com\n\n" + ("Official FIFA update. " * 300)

    async def poll_side_effect(agent_id, *, conversation_id, round_num, max_items=10):
        if round_num == 1:
            return [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-search",
                                "type": "function",
                                "function": {
                                    "name": "web_fetch",
                                    "arguments": '{"url":"https://example.com"}',
                                },
                            }
                        ],
                    },
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                }
            ]
        return [
            {
                "message": {"content": "Summary based on fetched page."},
                "usage": {},
            }
        ]

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.services.step_tools.fetch_web_page",
            new=AsyncMock(
                return_value={
                    "url": "https://example.com",
                    "final_url": "https://example.com",
                    "title": "Example",
                    "content_type": "text/html",
                    "text": large_tool_result,
                    "truncated": False,
                }
            ),
        ):
            result = await execute_step(
                cp,
                step_key="writer",
                system_prompt="You are a journalist.",
                user_content="Write about FIFA 2026 using current web sources",
                puller="puller-a",
                model="model-a",
                run_id="run-token-estimate",
            )

    assert result["usage"]["total_tokens"] > 120
    assert result["usage"]["input_tokens"] > 100


@pytest.mark.asyncio
async def test_execute_step_injects_tool_guidance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    cp.poll_responses = AsyncMock(
        return_value=[
            {
                "message": {"content": "Done."},
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        ]
    )

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        await execute_step(
            cp,
            step_key="writer",
            system_prompt="You are a journalist.",
            user_content="Write about FIFA 2026",
            puller="puller-a",
            model="model-a",
            run_id="run-guidance",
            enabled_tools={"write_file": False, "web_search": True},
            brave_search_api_key="brave-test",
        )

    first_task = cp.submit_task.await_args_list[0].args[0]
    system_message = first_task["messages"][0]["content"]
    assert "web_search" in system_message
    assert "Do not tell the user you lack web access" in system_message


@pytest.mark.asyncio
async def test_execute_step_nudges_tool_refusal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})

    async def poll_side_effect(agent_id, *, conversation_id, round_num, max_items=10):
        if round_num == 1:
            return [
                {
                    "message": {
                        "content": "I don't have the ability to search the web for current articles.",
                    },
                    "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
                }
            ]
        if round_num == 2:
            return [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-search",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"FIFA 2026 World Cup"}',
                                },
                            }
                        ],
                    },
                    "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                }
            ]
        return [
            {
                "message": {"content": "FIFA 2026 will be hosted across USA, Canada, and Mexico."},
                "usage": {"input_tokens": 6, "output_tokens": 8, "total_tokens": 14},
            }
        ]

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.services.step_tools.brave_web_search",
            new=AsyncMock(return_value={"web": {"results": [{"title": "FIFA", "url": "https://fifa.com", "description": "Official"}]}}),
        ):
            result = await execute_step(
                cp,
                step_key="writer",
                system_prompt="You are a journalist.",
                user_content="Write about FIFA 2026 using current web sources",
                puller="puller-a",
                model="model-a",
                run_id="run-nudge",
                enabled_tools={"write_file": False, "web_search": True},
                brave_search_api_key="brave-test",
            )

    assert cp.submit_task.await_count == 3
    second_task = cp.submit_task.await_args_list[1].args[0]
    nudge_messages = [
        message
        for message in second_task["messages"]
        if message.get("role") == "user" and "web_search" in str(message.get("content") or "")
    ]
    assert nudge_messages, "Expected refusal nudge before the tool-call round"
    third_task = cp.submit_task.await_args_list[2].args[0]
    assert third_task["messages"][-1]["role"] == "tool"
    assert result["content"] == "FIFA 2026 will be hosted across USA, Canada, and Mexico."
    assert result.get("turns") == 3


@pytest.mark.asyncio
async def test_execute_step_estimates_tokens_for_tool_round_without_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    cp = AsyncMock(spec=ControlPlaneClient)
    cp.submit_task = AsyncMock(return_value={"queue_depth": 1})
    large_tool_result = "URL: https://example.com\n\n" + ("Official FIFA update. " * 300)

    async def poll_side_effect(agent_id, *, conversation_id, round_num, max_items=10):
        if round_num == 1:
            return [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-search",
                                "type": "function",
                                "function": {
                                    "name": "web_fetch",
                                    "arguments": '{"url":"https://example.com"}',
                                },
                            }
                        ],
                    },
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                }
            ]
        return [
            {
                "message": {"content": "Summary based on fetched page."},
                "usage": {},
            }
        ]

    cp.poll_responses = AsyncMock(side_effect=poll_side_effect)

    with patch("article_factory.workers.executor.asyncio.sleep", new=AsyncMock()):
        with patch(
            "article_factory.services.step_tools.fetch_web_page",
            new=AsyncMock(
                return_value={
                    "url": "https://example.com",
                    "final_url": "https://example.com",
                    "title": "Example",
                    "content_type": "text/html",
                    "text": large_tool_result,
                    "truncated": False,
                }
            ),
        ):
            result = await execute_step(
                cp,
                step_key="writer",
                system_prompt="You are a journalist.",
                user_content="Write about FIFA 2026 using current web sources",
                puller="puller-a",
                model="model-a",
                run_id="run-token-estimate",
            )

    assert result["usage"]["total_tokens"] > 120
    assert result["usage"]["input_tokens"] > 100
