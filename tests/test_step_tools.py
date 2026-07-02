from __future__ import annotations

from pathlib import Path

import pytest

from article_factory.services.flow_tool_requirements import collect_flow_tool_requirements
from article_factory.services.step_tools import (
    StepToolRegistry,
    all_step_tools_enabled,
    augment_system_prompt_for_tools,
    build_step_tool_definitions,
    build_tool_system_guidance,
    looks_like_tool_refusal,
    normalize_step_enabled_tools,
    resolve_step_tools,
    resolve_workspace_path,
    run_workspace_root,
    tool_use_nudge_message,
)


def test_normalize_step_enabled_tools_defaults() -> None:
    assert normalize_step_enabled_tools(None) == {
        "write_file": False,
        "read_file": False,
        "web_search": False,
        "web_fetch": False,
    }


def test_resolve_step_tools_always_enables_all() -> None:
    assert resolve_step_tools(None) == all_step_tools_enabled()
    assert resolve_step_tools({"write_file": False, "web_search": False}) == all_step_tools_enabled()


def test_build_step_tool_definitions_all_tools() -> None:
    defs = build_step_tool_definitions(all_step_tools_enabled())
    names = [item["function"]["name"] for item in defs]
    assert names == ["write_file", "read_file", "list_files", "web_search", "web_fetch"]


def test_build_step_tool_definitions_includes_companion_file_tools() -> None:
    defs = build_step_tool_definitions({"write_file": True, "web_search": False, "web_fetch": False})
    names = {item["function"]["name"] for item in defs}
    assert names == {"write_file", "read_file", "list_files"}


def test_build_step_tool_definitions_web_search_only() -> None:
    defs = build_step_tool_definitions({"write_file": False, "web_search": True, "web_fetch": False})
    assert [item["function"]["name"] for item in defs] == ["web_search"]


def test_build_step_tool_definitions_web_fetch_only() -> None:
    defs = build_step_tool_definitions({"write_file": False, "web_search": False, "web_fetch": True})
    assert [item["function"]["name"] for item in defs] == ["web_fetch"]


def test_resolve_workspace_path_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        resolve_workspace_path(tmp_path, "../outside.txt")


@pytest.mark.asyncio
async def test_write_and_read_workspace_file(tmp_path: Path) -> None:
    registry = StepToolRegistry(workspace_root=tmp_path, brave_api_key="")
    write = await registry.execute(
        {
            "id": "call-1",
            "function": {"name": "write_file", "arguments": {"path": "notes.md", "content": "hello"}},
        }
    )
    assert "wrote" in write["content"]
    read = await registry.execute(
        {"id": "call-2", "function": {"name": "read_file", "arguments": {"path": "notes.md"}}}
    )
    assert read["content"] == "hello"


@pytest.mark.asyncio
async def test_web_search_without_api_key() -> None:
    registry = StepToolRegistry(workspace_root=Path("/tmp/unused"), brave_api_key="")
    result = await registry.execute(
        {"id": "call-3", "function": {"name": "web_search", "arguments": {"query": "cats"}}}
    )
    assert "not configured" in result["content"].lower()


def test_build_tool_system_guidance_web_search() -> None:
    guidance = build_tool_system_guidance({"write_file": False, "web_search": True})
    assert "web_search" in guidance
    assert "Do not tell the user you lack web access" in guidance


def test_augment_system_prompt_for_tools() -> None:
    result = augment_system_prompt_for_tools("You are a researcher.", {"web_search": True, "write_file": False})
    assert result.startswith("You are a researcher.")
    assert "web_search" in result


def test_augment_system_prompt_skips_when_no_tools() -> None:
    assert augment_system_prompt_for_tools("Base prompt", {"write_file": False, "web_search": False}) == "Base prompt"


def test_looks_like_tool_refusal() -> None:
    sample = "I don't have the ability to search the web or read current articles in real-time."
    assert looks_like_tool_refusal(sample) is True
    assert looks_like_tool_refusal("Here is the article draft.") is False


def test_tool_use_nudge_message() -> None:
    message = tool_use_nudge_message({"web_search": True, "write_file": False})
    assert "web_search" in message


def test_collect_flow_tool_requirements() -> None:
    reqs = collect_flow_tool_requirements()
    assert reqs["needs_web_search"] is True
    assert reqs["needs_write_file"] is True
    assert reqs["needs_web_fetch"] is True


def test_run_workspace_root_creates_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("article_factory.services.step_tools.settings.flow_run_outputs_root", str(tmp_path))
    root = run_workspace_root("run-abc")
    assert root.is_dir()
    assert root.name == "workspace"
