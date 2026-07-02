from __future__ import annotations

from article_factory.services.token_usage import aggregate_usage_stats, step_turns


def test_step_turns_from_field() -> None:
    assert step_turns({"turns": 3, "content": "done"}) == 3


def test_step_turns_from_tool_rounds() -> None:
    step = {
        "tools_used": [
            {"tool": "write_file", "round": 1},
            {"tool": "web_search", "round": 2},
        ],
        "content": "done",
    }
    assert step_turns(step) == 2


def test_step_turns_defaults_to_one_for_content() -> None:
    assert step_turns({"content": "article body"}) == 1


def test_aggregate_usage_stats_sums_turns() -> None:
    steps = [
        {"turns": 2, "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}},
        {"turns": 1, "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}},
    ]
    stats = aggregate_usage_stats(steps)
    assert stats["total_turns"] == 3
    assert stats["llm_calls"] == 3
