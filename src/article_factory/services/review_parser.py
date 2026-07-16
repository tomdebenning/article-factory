"""Structured review parsing for telemetry and analytics."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from article_factory.services.verdict import Verdict, parse_verdict

logger = logging.getLogger(__name__)

BEGIN_REVIEW_JSON = "BEGIN REVIEW JSON"
END_REVIEW_JSON = "END REVIEW JSON"

REVIEW_JSON_SCHEMA_VERSION = 1

CRITERION_SPECS: list[tuple[str, str, int]] = [
    ("accuracy_verifiable_facts", "Accuracy & Verifiable Facts", 40),
    ("organization_flow", "Organization & Flow", 15),
    ("writing_quality", "Writing Quality", 15),
    ("depth_specificity", "Depth & Specificity", 15),
    ("reader_engagement", "Reader Engagement", 10),
    ("grammar_mechanics", "Grammar & Mechanics", 5),
]

CRITERION_LABEL_ALIASES: dict[str, str] = {
    "accuracy and verifiable facts": "accuracy_verifiable_facts",
    "accuracy & verifiable facts": "accuracy_verifiable_facts",
    "organization and flow": "organization_flow",
    "organization & flow": "organization_flow",
    "writing quality": "writing_quality",
    "depth and specificity": "depth_specificity",
    "depth & specificity": "depth_specificity",
    "reader engagement": "reader_engagement",
    "grammar and mechanics": "grammar_mechanics",
    "grammar & mechanics": "grammar_mechanics",
}

TOTAL_SCORE_INLINE = re.compile(
    r"TOTAL\s+SCORE\s*:\s*\*?\*?(\d{1,3})\s*/\s*(\d{1,3})\*?\*?",
    re.IGNORECASE,
)
TOTAL_SCORE_BLOCK = re.compile(
    r"TOTAL\s+SCORE\s*\n+\s*(\d{1,3})\s*/\s*(\d{1,3})",
    re.IGNORECASE,
)
CRITERION_SCORE = re.compile(
    r"(\d{1,3})\s*/\s*(\d{1,3})",
)

REVIEW_JSON_BLOCK = re.compile(
    rf"{re.escape(BEGIN_REVIEW_JSON)}\s*(.*?)\s*{re.escape(END_REVIEW_JSON)}",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class CriterionScore:
    criterion_key: str
    criterion_label: str
    score: int
    max_score: int
    comment: str = ""


@dataclass
class ReviewIssue:
    issue_number: int
    category: str = ""
    status: str = "unknown"
    problem: str = ""
    why_it_loses_points: str = ""
    required_change: str = ""


@dataclass
class StructuredReview:
    schema_version: int | None = None
    total_score: int | None = None
    verdict: str | None = None
    runtime_verdict: Verdict = Verdict.NONE
    criteria: list[CriterionScore] = field(default_factory=list)
    previous_issues: list[ReviewIssue] = field(default_factory=list)
    required_changes: list[ReviewIssue] = field(default_factory=list)
    structured_review_valid: bool = False
    parse_warnings: list[str] = field(default_factory=list)
    source: str = "none"


def review_json_prompt_instructions() -> str:
    return (
        "\n\n============================================================\n"
        "MACHINE-READABLE TELEMETRY (required)\n"
        "============================================================\n\n"
        "Immediately BEFORE the line ``END REVIEW``, include a JSON block "
        "between these exact markers:\n\n"
        f"{BEGIN_REVIEW_JSON}\n"
        "{\n"
        '  "schema_version": 1,\n'
        '  "total_score": 95,\n'
        '  "verdict": "accepted",\n'
        '  "criteria": {\n'
        '    "accuracy_verifiable_facts": {"score": 38, "max_score": 40},\n'
        '    "organization_flow": {"score": 14, "max_score": 15},\n'
        '    "writing_quality": {"score": 14, "max_score": 15},\n'
        '    "depth_specificity": {"score": 14, "max_score": 15},\n'
        '    "reader_engagement": {"score": 10, "max_score": 10},\n'
        '    "grammar_mechanics": {"score": 5, "max_score": 5}\n'
        "  },\n"
        '  "previous_issues": [{"issue_number": 1, "status": "fixed"}],\n'
        '  "required_changes": []\n'
        "}\n"
        f"{END_REVIEW_JSON}\n\n"
        "Rules: schema_version must be 1; total_score 0-100; verdict is "
        '"accepted" or "rejected"; all six criteria keys required; '
        "required_changes must be [] when accepted; keep VERDICT: ACCEPTED "
        "or VERDICT: REJECTED on its own line for runtime loop control."
    )


def _normalize_verdict_token(value: str) -> str | None:
    token = (value or "").strip().lower()
    if token in {"accept", "accepted"}:
        return "accepted"
    if token in {"reject", "rejected"}:
        return "rejected"
    return None


def _normalize_issue_status(value: str) -> str:
    token = re.sub(r"[^a-z_]+", "_", (value or "").strip().lower()).strip("_")
    allowed = {"fixed", "partially_fixed", "not_fixed", "regressed", "new", "unknown"}
    if token in allowed:
        return token
    if "partial" in token:
        return "partially_fixed"
    if "fix" in token:
        return "fixed"
    if "regress" in token:
        return "regressed"
    if "not" in token:
        return "not_fixed"
    return "unknown"


def _validate_json_review(data: dict[str, Any], warnings: list[str]) -> bool:
    if int(data.get("schema_version") or 0) != REVIEW_JSON_SCHEMA_VERSION:
        warnings.append("schema_version must be 1")
        return False

    total = data.get("total_score")
    if not isinstance(total, int) or total < 0 or total > 100:
        warnings.append("total_score must be an integer from 0 through 100")
        return False

    verdict = _normalize_verdict_token(str(data.get("verdict") or ""))
    if verdict is None:
        warnings.append("verdict must be accepted or rejected")
        return False

    criteria = data.get("criteria")
    if not isinstance(criteria, dict):
        warnings.append("criteria must be an object")
        return False

    for key, _label, default_max in CRITERION_SPECS:
        entry = criteria.get(key)
        if not isinstance(entry, dict):
            warnings.append(f"missing criterion {key}")
            return False
        score = entry.get("score")
        max_score = entry.get("max_score")
        if not isinstance(score, int) or not isinstance(max_score, int):
            warnings.append(f"criterion {key} score/max_score must be integers")
            return False
        if score < 0 or score > max_score:
            warnings.append(f"criterion {key} score out of bounds")
            return False
        if max_score != default_max:
            warnings.append(f"criterion {key} max_score expected {default_max}")

    if verdict == "accepted" and data.get("required_changes"):
        if isinstance(data["required_changes"], list) and data["required_changes"]:
            warnings.append("accepted review must have empty required_changes")
            return False

    return len([w for w in warnings if w.startswith("missing") or "must be" in w]) == 0


def _parse_json_block(content: str) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    matches = list(REVIEW_JSON_BLOCK.finditer(content or ""))
    if not matches:
        return None, warnings

    for match in reversed(matches):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            warnings.append(f"malformed JSON block: {exc}")
            continue
        if not isinstance(data, dict):
            warnings.append("JSON block must be an object")
            continue
        block_warnings: list[str] = []
        if _validate_json_review(data, block_warnings):
            return data, warnings
        warnings.extend(block_warnings)
    return None, warnings


def _parse_legacy_total_score(content: str) -> int | None:
    text = content or ""
    for pattern in (TOTAL_SCORE_INLINE, TOTAL_SCORE_BLOCK):
        matches = list(pattern.finditer(text))
        if matches:
            score = int(matches[-1].group(1))
            if 0 <= score <= 100:
                return score
    return None


def _criterion_key_from_label(label: str) -> str | None:
    normalized = re.sub(r"\*+", "", label).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized).strip(" :")
    if normalized in CRITERION_LABEL_ALIASES:
        return CRITERION_LABEL_ALIASES[normalized]
    for key, canonical, _max in CRITERION_SPECS:
        if canonical.lower() == normalized:
            return key
    return None


def _parse_legacy_criteria(content: str) -> list[CriterionScore]:
    text = content or ""
    results: list[CriterionScore] = []
    for key, label, default_max in CRITERION_SPECS:
        pattern = re.compile(
            rf"{re.escape(label)}\s*\n+\s*(\d{{1,3}})\s*/\s*(\d{{1,3}})",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if not match:
            alt = re.compile(
                rf"{re.escape(label)}[:\s]+\*?\*?(\d{{1,3}})\s*/\s*(\d{{1,3}})",
                re.IGNORECASE,
            )
            match = alt.search(text)
        if match:
            results.append(
                CriterionScore(
                    criterion_key=key,
                    criterion_label=label,
                    score=int(match.group(1)),
                    max_score=int(match.group(2)),
                )
            )
            continue
        for line in text.splitlines():
            if label.lower() not in line.lower():
                continue
            score_match = CRITERION_SCORE.search(line)
            if score_match:
                results.append(
                    CriterionScore(
                        criterion_key=key,
                        criterion_label=label,
                        score=int(score_match.group(1)),
                        max_score=int(score_match.group(2)),
                    )
                )
                break
    return results


def _parse_issues_from_json(items: Any, *, required: bool) -> list[ReviewIssue]:
    if not isinstance(items, list):
        return []
    issues: list[ReviewIssue] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        issue_number = int(raw.get("issue_number") or len(issues) + 1)
        issues.append(
            ReviewIssue(
                issue_number=issue_number,
                category=str(raw.get("category") or ""),
                status=_normalize_issue_status(str(raw.get("status") or "unknown")),
                problem=str(raw.get("problem") or ""),
                why_it_loses_points=str(raw.get("why_it_loses_points") or ""),
                required_change=str(raw.get("required_change") or ""),
            )
        )
    if required:
        for issue in issues:
            if not issue.required_change and issue.problem:
                issue.required_change = issue.problem
    return issues


def _count_issue_statuses(issues: list[ReviewIssue]) -> dict[str, int]:
    counts = {
        "fixed": 0,
        "partially_fixed": 0,
        "not_fixed": 0,
        "regressed": 0,
        "new": 0,
        "unknown": 0,
    }
    for issue in issues:
        key = issue.status if issue.status in counts else "unknown"
        counts[key] += 1
    return counts


def parse_structured_review(content: str) -> StructuredReview | None:
    """Parse reviewer output into structured telemetry fields."""
    text = (content or "").strip()
    if not text:
        return None

    result = StructuredReview(runtime_verdict=parse_verdict(text))
    json_data, json_warnings = _parse_json_block(text)
    result.parse_warnings.extend(json_warnings)

    if json_data:
        result.schema_version = REVIEW_JSON_SCHEMA_VERSION
        result.total_score = int(json_data["total_score"])
        result.verdict = _normalize_verdict_token(str(json_data.get("verdict") or ""))
        result.structured_review_valid = True
        result.source = "json"

        criteria_obj = json_data.get("criteria") or {}
        for key, label, default_max in CRITERION_SPECS:
            entry = criteria_obj.get(key) or {}
            result.criteria.append(
                CriterionScore(
                    criterion_key=key,
                    criterion_label=label,
                    score=int(entry.get("score") or 0),
                    max_score=int(entry.get("max_score") or default_max),
                )
            )
        result.previous_issues = _parse_issues_from_json(json_data.get("previous_issues"), required=False)
        result.required_changes = _parse_issues_from_json(json_data.get("required_changes"), required=True)
    else:
        legacy_score = _parse_legacy_total_score(text)
        if legacy_score is not None:
            result.total_score = legacy_score
            result.source = "legacy"
        legacy_criteria = _parse_legacy_criteria(text)
        if legacy_criteria:
            result.criteria = legacy_criteria
            if result.source == "none":
                result.source = "legacy"
        if result.runtime_verdict == Verdict.ACCEPT:
            result.verdict = "accepted"
        elif result.runtime_verdict == Verdict.REJECT:
            result.verdict = "rejected"

    if result.verdict and result.runtime_verdict != Verdict.NONE:
        runtime = "accepted" if result.runtime_verdict == Verdict.ACCEPT else "rejected"
        if result.verdict != runtime:
            result.parse_warnings.append(
                f"JSON/legacy verdict ({result.verdict}) conflicts with VERDICT line ({runtime})"
            )
            logger.warning(
                "Review verdict mismatch: structured=%s runtime=%s",
                result.verdict,
                runtime,
            )

    if result.source == "none" and result.runtime_verdict == Verdict.NONE and result.total_score is None:
        return None

    return result


def issue_resolution_counts(review: StructuredReview | None) -> dict[str, int]:
    if review is None:
        return {
            "fixed_issue_count": 0,
            "partially_fixed_issue_count": 0,
            "not_fixed_issue_count": 0,
            "regressed_issue_count": 0,
            "required_change_count": 0,
        }
    previous = _count_issue_statuses(review.previous_issues)
    return {
        "fixed_issue_count": previous["fixed"],
        "partially_fixed_issue_count": previous["partially_fixed"],
        "not_fixed_issue_count": previous["not_fixed"],
        "regressed_issue_count": previous["regressed"],
        "required_change_count": len(review.required_changes),
    }
