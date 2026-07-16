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


def test_parse_verdict_markdown_heading() -> None:
    text = "Score is 90/100.\n\n---\n\n## VERDICT: ACCEPT"
    assert parse_verdict(text) == Verdict.ACCEPT


def test_parse_verdict_markdown_h3_heading() -> None:
    text = "Needs work.\n\n### VERDICT: REJECT"
    assert parse_verdict(text) == Verdict.REJECT


def test_parse_verdict_rejected_and_accepted_forms() -> None:
    text = "Score 52/100.\n\nVERDICT: REJECTED\n\nMore feedback.\n\nVERDICT: REJECT"
    assert parse_verdict(text) == Verdict.REJECT

    accepted = "Strong draft.\n\nVERDICT: ACCEPTED"
    assert parse_verdict(accepted) == Verdict.ACCEPT


def test_parse_verdict_rejected_before_degenerate_tail() -> None:
    text = (
        "ARTICLE REVIEW\n\nAccuracy: 6/30\n\n---\n\nVERDICT: REJECTED\n\n"
        + "EDITOR FEEDBACK\n\nFix accuracy.\n\n"
        + ("word " * 5000)
    )
    assert parse_verdict(text) == Verdict.REJECT
