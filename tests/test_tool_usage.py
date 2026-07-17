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


def test_summarize_tool_detail_variants() -> None:
    assert summarize_tool_detail("web_search", {"query": "news"}) == '"news"'
    assert summarize_tool_detail("web_fetch", {"url": "https://example.com"}) == "https://example.com"
    assert summarize_tool_detail("read_file", {"path": "notes.md"}) == "notes.md"
    assert summarize_tool_detail("list_files", {"path": "drafts"}) == "drafts"
    assert summarize_tool_detail("custom_tool", {"value": "payload"}) == "payload"
    assert summarize_tool_detail("custom_tool", {}) == ""


def test_aggregate_tool_use_skips_invalid_entries() -> None:
    steps = [
        {
            "step_key": "writer",
            "tools_used": ["bad", {"tool": "write_file"}, {"tool": "write_file"}],
        }
    ]
    summary = aggregate_tool_use_by_step(steps)
    assert summary[0]["tools"] == ["Write file"]


def test_flatten_tool_labels() -> None:
    from article_factory.services.tool_usage import flatten_tool_labels

    summary = [{"step_key": "writer", "step_name": "Writer", "tools": ["Web search"]}]
    assert flatten_tool_labels(summary) == ["Web search (Writer)"]


def test_merge_tools_into_manifest_from_executions() -> None:
    from article_factory.services.step_trace import merge_tools_into_manifest

    manifest = {
        "step_stats": [
            {"step_key": "writer", "step_name": "Writer", "content": "draft"},
            {"step_key": "review", "step_name": "Review", "content": "ok"},
        ]
    }
    executions = [
        {
            "step_key": "writer",
            "tools_used": [{"tool": "web_search", "label": "Web search", "detail": '"news"'}],
        },
        {"step_key": "review", "tools_used": []},
    ]
    merged = merge_tools_into_manifest(manifest, executions)
    assert merged["step_stats"][0]["tools_used"][0]["tool"] == "web_search"
    assert merged["step_stats"][1].get("tools_used", []) == []


def test_manifest_step_tools_backfilled() -> None:
    from article_factory.services.step_trace import manifest_step_tools_backfilled

    before = {"step_stats": [{"step_key": "writer", "content": "draft"}]}
    after = {
        "step_stats": [
            {
                "step_key": "writer",
                "content": "draft",
                "tools_used": [{"tool": "web_search"}],
            }
        ]
    }
    assert manifest_step_tools_backfilled(before, after) is True
    assert manifest_step_tools_backfilled(after, after) is False
