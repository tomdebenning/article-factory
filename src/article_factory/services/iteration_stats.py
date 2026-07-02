from __future__ import annotations

from typing import Any

from article_factory.services.verdict import Verdict, parse_verdict


def _step_content(step: dict[str, Any]) -> str:
    return str(step.get("content") or step.get("response_content") or "")


def _aggregate_usage_stats(steps: list[dict[str, Any]]) -> dict[str, Any]:
    from article_factory.services.token_usage import aggregate_usage_stats

    return aggregate_usage_stats(steps)


def build_iteration_stats(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group pipeline steps into writer–review cycles for manifest display."""
    if not steps:
        return []

    has_review = any(str(step.get("step_key") or "") == "review" for step in steps)
    if not has_review:
        return [
            {
                "iteration": 1,
                "draft_number": 1,
                "steps": steps,
                "stats": _aggregate_usage_stats(steps),
                "verdict": None,
                "accepted": True,
            }
        ]

    iterations: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []
    iteration_num = 0

    for step in steps:
        buffer.append(step)
        if str(step.get("step_key") or "") != "review":
            continue

        iteration_num += 1
        verdict = parse_verdict(_step_content(step))
        iterations.append(
            {
                "iteration": iteration_num,
                "draft_number": sum(1 for entry in buffer if str(entry.get("step_key") or "") == "writer"),
                "steps": buffer,
                "stats": _aggregate_usage_stats(buffer),
                "verdict": verdict.value if verdict != Verdict.NONE else None,
                "accepted": verdict == Verdict.ACCEPT,
            }
        )
        buffer = []

    if buffer:
        iteration_num += 1
        iterations.append(
            {
                "iteration": iteration_num,
                "draft_number": sum(1 for entry in buffer if str(entry.get("step_key") or "") == "writer"),
                "steps": buffer,
                "stats": _aggregate_usage_stats(buffer),
                "verdict": None,
                "accepted": False,
            }
        )

    return iterations


def production_summary(
    steps: list[dict[str, Any]],
    *,
    draft_number: int = 0,
    review_round: int = 0,
) -> dict[str, Any]:
    iteration_stats = build_iteration_stats(steps)
    draft_count = sum(1 for step in steps if str(step.get("step_key") or "") == "writer")
    review_count = sum(1 for step in steps if str(step.get("step_key") or "") == "review")
    reject_count = sum(
        1
        for step in steps
        if str(step.get("step_key") or "") == "review"
        and parse_verdict(_step_content(step)) == Verdict.REJECT
    )
    iteration_count = len(iteration_stats)
    multi_pass = draft_count > 1 or review_count > 1 or iteration_count > 1

    resolved_draft_number = max(draft_number, draft_count, 1)
    resolved_review_round = max(review_round, reject_count)

    return {
        "multi_pass": multi_pass,
        "iteration_count": iteration_count,
        "draft_count": max(draft_count, resolved_draft_number if multi_pass else 1),
        "review_count": review_count,
        "review_round": resolved_review_round,
        "draft_number": resolved_draft_number,
    }


def attach_iteration_metadata(
    manifest: dict[str, Any],
    *,
    draft_number: int = 0,
    review_round: int = 0,
) -> dict[str, Any]:
    data = dict(manifest or {})
    steps = list(data.get("step_stats") or data.get("steps") or [])
    iteration_stats = build_iteration_stats(steps)
    production = production_summary(
        steps,
        draft_number=int(data.get("draft_number") or draft_number or 0),
        review_round=int(data.get("review_round") or review_round or 0),
    )
    data["iteration_stats"] = iteration_stats
    data["production"] = production
    data["draft_number"] = production["draft_number"]
    data["review_round"] = production["review_round"]
    return data
