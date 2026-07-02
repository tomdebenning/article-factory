from __future__ import annotations

from article_factory.services.verdict import Verdict, extract_feedback_body, parse_verdict


def test_parse_verdict_last_match_wins() -> None:
    text = "We could reject this draft.\n\nMore notes.\n\nVERDICT: ACCEPT"
    assert parse_verdict(text) == Verdict.ACCEPT


def test_parse_verdict_reject() -> None:
    text = "Please rewrite the lede.\n\nVERDICT: REJECT"
    assert parse_verdict(text) == Verdict.REJECT


def test_extract_feedback_body() -> None:
    text = "Please rewrite the lede.\n\nVERDICT: REJECT"
    assert extract_feedback_body(text) == "Please rewrite the lede."


def test_parse_verdict_markdown_bold() -> None:
    text = "Looks good after edits.\n\n---\n\n**VERDICT: ACCEPT**"
    assert parse_verdict(text) == Verdict.ACCEPT
