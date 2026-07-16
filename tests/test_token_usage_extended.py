from __future__ import annotations

from article_factory.services.token_usage import (
    aggregate_usage_stats,
    enrich_manifest,
    enrich_step_record,
    estimate_tokens_from_text,
    finalize_stats,
    normalize_round_usage,
    normalize_usage,
    serialize_messages_for_token_estimate,
    serialize_tool_calls,
    serialize_tools_for_token_estimate,
    step_turns,
)


def test_estimate_tokens_from_text_empty() -> None:
    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("   ") == 0


def test_serialize_tool_calls_empty_and_fallback() -> None:
    assert serialize_tool_calls(None) == ""
    assert serialize_tool_calls([{"fn": object()}])  # TypeError path -> str


def test_serialize_tools_for_token_estimate_empty() -> None:
    assert serialize_tools_for_token_estimate(None) == ""


def test_serialize_messages_skips_non_dict() -> None:
    text = serialize_messages_for_token_estimate(["bad", {"role": "user", "content": "hi"}])
    assert "[user]" in text
    assert "hi" in text


def test_serialize_messages_includes_thinking() -> None:
    text = serialize_messages_for_token_estimate(
        [{"role": "assistant", "content": "", "thinking": "internal reasoning"}]
    )
    assert "internal reasoning" in text


def test_normalize_round_usage_estimates_partial_usage() -> None:
    usage = normalize_round_usage(
        {"total_tokens": 100},
        messages=[{"role": "user", "content": "A" * 200}],
        assistant_message={"content": "B" * 100},
        tools_text='{"name": "tool"}',
    )
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] >= usage["input_tokens"] + usage["output_tokens"] - 1


def test_normalize_round_usage_all_zero_estimates() -> None:
    usage = normalize_round_usage(
        {},
        messages=[{"role": "user", "content": "prompt text here"}],
        assistant_message={"content": "response text"},
    )
    assert usage["total_tokens"] > 0


def test_normalize_usage_output_only_estimates_input() -> None:
    usage = normalize_usage(None, input_text="", output_text="A" * 40)
    assert usage["input_tokens"] >= 32
    assert usage["output_tokens"] > 0


def test_normalize_usage_partial_total() -> None:
    usage = normalize_usage({"input_tokens": 10, "output_tokens": 5, "total_tokens": 0})
    assert usage["total_tokens"] == 15


def test_finalize_stats_total_from_parts() -> None:
    assert finalize_stats({"input_tokens": 10, "output_tokens": 5, "total_tokens": 0})["total_tokens"] == 15


def test_finalize_stats_splits_total_only() -> None:
    stats = finalize_stats({"total_tokens": 100})
    assert stats["input_tokens"] > 0
    assert stats["output_tokens"] > 0


def test_finalize_stats_input_only() -> None:
    stats = finalize_stats({"input_tokens": 80, "total_tokens": 100})
    assert stats["output_tokens"] == 20


def test_finalize_stats_output_only() -> None:
    stats = finalize_stats({"output_tokens": 75, "total_tokens": 100})
    assert stats["input_tokens"] == 25


def test_step_turns_from_tools() -> None:
    assert step_turns({"tools_used": [{"round": 3}, {"round": 1}]}) == 3


def test_step_turns_from_content() -> None:
    assert step_turns({"content": "hello"}) == 1
    assert step_turns({}) == 0


def test_enrich_step_record_with_tool_text() -> None:
    step = enrich_step_record(
        {
            "step_key": "writer",
            "tools_used": [{"tool": "web_search", "detail": "query results"}],
            "usage": {},
        }
    )
    assert step["usage"]["total_tokens"] > 0


def test_enrich_manifest_from_body_only() -> None:
    manifest = enrich_manifest(
        {"stats": {"total_duration_ms": 500}},
        selected_model="model-x",
        body_markdown="# Title\n\nArticle body text here.",
    )
    assert manifest["steps"][0]["step_key"] == "writer"
    assert manifest["stats"]["total_tokens"] > 0


def test_aggregate_usage_stats() -> None:
    totals = aggregate_usage_stats(
        [
            {
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "duration_ms": 100,
                "turns": 2,
            }
        ]
    )
    assert totals["total_tokens"] == 15
    assert totals["total_turns"] == 2
    assert totals["llm_calls"] == 2
