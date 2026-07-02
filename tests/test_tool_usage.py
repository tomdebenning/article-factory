from __future__ import annotations

from article_factory.services.tool_usage import (
    aggregate_tool_use_by_step,
    summarize_tool_detail,
    tool_label,
    tool_use_entry,
    unique_tool_labels,
)


def test_tool_use_entry() -> None:
    entry = tool_use_entry("web_search", {"query": "latest news", "count": 3}, result="ok", round_num=2)
    assert entry["tool"] == "web_search"
    assert entry["label"] == "Web search"
    assert entry["detail"] == '"latest news" (3 results)'
    assert entry["round"] == 2
    assert entry["ok"] is True


def test_tool_use_entry_error() -> None:
    entry = tool_use_entry("write_file", {"path": "x.md"}, result="Error: denied", round_num=1)
    assert entry["ok"] is False


def test_summarize_tool_detail_write_file() -> None:
    assert summarize_tool_detail("write_file", {"path": "draft.md"}) == "draft.md"


def test_aggregate_tool_use_by_step() -> None:
    steps = [
        {
            "step_key": "writer",
            "step_name": "Writer",
            "tools_used": [
                {"tool": "write_file", "label": "Write file"},
                {"tool": "write_file", "label": "Write file"},
                {"tool": "web_search"},
            ],
        },
        {"step_key": "review", "tools_used": []},
    ]
    summary = aggregate_tool_use_by_step(steps)
    assert len(summary) == 1
    assert summary[0]["step_key"] == "writer"
    assert summary[0]["tools"] == ["Write file", "Web search"]


def test_unique_tool_labels() -> None:
    summary = [
        {"step_key": "writer", "tools": ["Write file", "Web search"]},
        {"step_key": "editor", "tools": ["Write file"]},
    ]
    assert unique_tool_labels(summary) == ["Write file", "Web search"]
