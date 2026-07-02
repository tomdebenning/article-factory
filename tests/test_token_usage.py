from __future__ import annotations

from article_factory.services.token_usage import (
    enrich_manifest,
    normalize_round_usage,
    normalize_usage,
    serialize_messages_for_token_estimate,
)


def test_normalize_usage_estimates_when_missing() -> None:
    usage = normalize_usage(None, input_text="prompt", output_text="Hello world from the model")
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_normalize_round_usage_counts_tool_messages() -> None:
    tool_body = "URL: https://example.com\n\n" + ("Fetched page text. " * 400)
    usage = normalize_round_usage(
        {},
        messages=[
            {"role": "system", "content": "You are a researcher."},
            {"role": "user", "content": "Summarize FIFA 2026"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_fetch"}}]},
            {"role": "tool", "content": tool_body, "name": "web_fetch"},
        ],
        assistant_message={"role": "assistant", "content": "FIFA 2026 will be hosted across North America."},
    )
    assert usage["input_tokens"] > 500
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_serialize_messages_for_token_estimate_includes_tool_role() -> None:
    text = serialize_messages_for_token_estimate(
        [{"role": "tool", "content": "search results here", "name": "web_search"}]
    )
    assert "[tool]" in text
    assert "search results here" in text


def test_enrich_manifest_adds_model_and_stats() -> None:
    manifest = enrich_manifest(
        {
            "steps": [
                {
                    "step_key": "step_1",
                    "content": "Cars revolutionized travel across the country.",
                    "usage": {},
                }
            ]
        },
        selected_model="qwen3.5:27b",
        body_markdown="Cars revolutionized travel across the country.",
    )
    assert manifest["selected_model"] == "qwen3.5:27b"
    assert manifest["stats"]["total_tokens"] > 0
    assert manifest["stats"]["input_tokens"] > 0
    assert manifest["stats"]["output_tokens"] > 0
