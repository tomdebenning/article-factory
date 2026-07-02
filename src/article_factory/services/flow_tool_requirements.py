from __future__ import annotations

from typing import Any


def collect_flow_tool_requirements(*, flows_root: str | None = None) -> dict[str, bool]:
    """All factory prompts receive the full tool set."""
    _ = flows_root
    return {
        "needs_write_file": True,
        "needs_read_file": True,
        "needs_web_search": True,
        "needs_web_fetch": True,
    }


def flow_dict_tool_requirements(flow: dict[str, Any]) -> dict[str, bool]:
    _ = flow
    return collect_flow_tool_requirements()
