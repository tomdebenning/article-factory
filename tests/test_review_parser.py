from __future__ import annotations

import json

from article_factory.services.review_parser import (
    BEGIN_REVIEW_JSON,
    END_REVIEW_JSON,
    issue_resolution_counts,
    parse_structured_review,
    review_json_prompt_instructions,
)
from article_factory.services.verdict import Verdict


def _json_block(payload: dict) -> str:
    return f"{BEGIN_REVIEW_JSON}\n{json.dumps(payload)}\n{END_REVIEW_JSON}"


def _full_review(*, verdict: str, total_score: int, required_changes: list | None = None) -> str:
    payload = {
        "schema_version": 1,
        "total_score": total_score,
        "verdict": verdict,
        "criteria": {
            "accuracy_verifiable_facts": {"score": 38, "max_score": 40},
            "organization_flow": {"score": 14, "max_score": 15},
            "writing_quality": {"score": 14, "max_score": 15},
            "depth_specificity": {"score": 14, "max_score": 15},
            "reader_engagement": {"score": 10, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [],
        "required_changes": required_changes or [],
    }
    verdict_line = "VERDICT: ACCEPTED" if verdict == "accepted" else "VERDICT: REJECTED"
    return (
        "ARTICLE REVIEW\n\nTOTAL SCORE\n\n"
        f"{total_score} / 100\n\n{_json_block(payload)}\n\n{verdict_line}\nEND REVIEW"
    )


def test_parse_valid_json_block() -> None:
    review = parse_structured_review(_full_review(verdict="accepted", total_score=95))
    assert review is not None
    assert review.structured_review_valid is True
    assert review.total_score == 95
    assert review.verdict == "accepted"
    assert len(review.criteria) == 6


def test_parse_malformed_json_falls_back_to_legacy() -> None:
    text = (
        "ARTICLE REVIEW\n\nAccuracy & Verifiable Facts\n\n38 / 40\n\n"
        "TOTAL SCORE\n\n88 / 100\n\n"
        f"{BEGIN_REVIEW_JSON}\n{{not json}}\n{END_REVIEW_JSON}\n\n"
        "VERDICT: REJECTED\nEND REVIEW"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.structured_review_valid is False
    assert review.total_score == 88
    assert review.verdict == "rejected"


def test_parse_legacy_only_review() -> None:
    text = (
        "ARTICLE REVIEW\n\nAccuracy & Verifiable Facts\n\n35 / 40\n\n"
        "Organization & Flow\n\n15 / 15\n\n"
        "TOTAL SCORE: 95/100\n\nVERDICT: ACCEPTED\nEND REVIEW"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.total_score == 95
    assert review.verdict == "accepted"
    assert review.structured_review_valid is False


def test_conflicting_verdict_records_warning() -> None:
    text = _full_review(verdict="accepted", total_score=100).replace(
        "VERDICT: ACCEPTED", "VERDICT: REJECTED"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.runtime_verdict == Verdict.REJECT
    assert review.verdict == "accepted"
    assert any("conflicts" in warning for warning in review.parse_warnings)


def test_blank_response_returns_none() -> None:
    assert parse_structured_review("") is None
    assert parse_structured_review("   ") is None


def test_last_json_block_wins() -> None:
    first = _json_block(
        {
            "schema_version": 1,
            "total_score": 50,
            "verdict": "rejected",
            "criteria": {
                "accuracy_verifiable_facts": {"score": 20, "max_score": 40},
                "organization_flow": {"score": 5, "max_score": 15},
                "writing_quality": {"score": 5, "max_score": 15},
                "depth_specificity": {"score": 5, "max_score": 15},
                "reader_engagement": {"score": 5, "max_score": 10},
                "grammar_mechanics": {"score": 5, "max_score": 5},
            },
            "previous_issues": [],
            "required_changes": [{"issue_number": 1, "category": "x", "problem": "p", "why_it_loses_points": "w", "required_change": "c"}],
        }
    )
    second = _json_block(
        {
            "schema_version": 1,
            "total_score": 92,
            "verdict": "accepted",
            "criteria": {
                "accuracy_verifiable_facts": {"score": 38, "max_score": 40},
                "organization_flow": {"score": 14, "max_score": 15},
                "writing_quality": {"score": 14, "max_score": 15},
                "depth_specificity": {"score": 14, "max_score": 15},
                "reader_engagement": {"score": 10, "max_score": 10},
                "grammar_mechanics": {"score": 5, "max_score": 5},
            },
            "previous_issues": [],
            "required_changes": [],
        }
    )
    text = f"{first}\n\n{second}\n\nVERDICT: ACCEPTED\nEND REVIEW"
    review = parse_structured_review(text)
    assert review is not None
    assert review.total_score == 92
    assert review.verdict == "accepted"


def test_accepted_with_required_changes_invalid_json() -> None:
    payload = {
        "schema_version": 1,
        "total_score": 95,
        "verdict": "accepted",
        "criteria": {
            "accuracy_verifiable_facts": {"score": 38, "max_score": 40},
            "organization_flow": {"score": 14, "max_score": 15},
            "writing_quality": {"score": 14, "max_score": 15},
            "depth_specificity": {"score": 14, "max_score": 15},
            "reader_engagement": {"score": 10, "max_score": 10},
            "grammar_mechanics": {"score": 5, "max_score": 5},
        },
        "previous_issues": [],
        "required_changes": [
            {
                "issue_number": 1,
                "category": "Accuracy",
                "problem": "bad",
                "why_it_loses_points": "why",
                "required_change": "fix",
            }
        ],
    }
    text = f"{_json_block(payload)}\n\nVERDICT: ACCEPTED\nEND REVIEW"
    review = parse_structured_review(text)
    assert review is not None
    assert review.structured_review_valid is False


def test_review_json_prompt_instructions_contains_markers() -> None:
    text = review_json_prompt_instructions()
    assert BEGIN_REVIEW_JSON in text
    assert END_REVIEW_JSON in text
    assert "schema_version" in text


def test_issue_resolution_counts_none() -> None:
    counts = issue_resolution_counts(None)
    assert counts["fixed_issue_count"] == 0
    assert counts["required_change_count"] == 0


def test_issue_resolution_counts_with_issues() -> None:
    review = parse_structured_review(
        _full_review(
            verdict="rejected",
            total_score=70,
            required_changes=[
                {
                    "issue_number": 1,
                    "category": "Accuracy",
                    "problem": "Wrong stat",
                    "why_it_loses_points": "Misleading",
                    "required_change": "Fix stat",
                }
            ],
        )
    )
    assert review is not None
    counts = issue_resolution_counts(review)
    assert counts["required_change_count"] == 1


def test_parse_legacy_criterion_inline_format() -> None:
    text = (
        "Accuracy & Verifiable Facts: **35 / 40**\n"
        "TOTAL SCORE: 85/100\n\nVERDICT: ACCEPTED\nEND REVIEW"
    )
    review = parse_structured_review(text)
    assert review is not None
    assert review.total_score == 85
