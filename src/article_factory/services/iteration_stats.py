from __future__ import annotations

from typing import Any

from article_factory.services.flow_roles import FlowRoleConfig, group_steps_into_iterations, resolve_flow_roles
from article_factory.services.flow_schema import FlowDefinition
from article_factory.services.verdict import Verdict, parse_verdict


def _step_content(step: dict[str, Any]) -> str:
    return str(step.get("content") or step.get("response_content") or "")


def _aggregate_usage_stats(steps: list[dict[str, Any]]) -> dict[str, Any]:
    from article_factory.services.token_usage import aggregate_usage_stats

    return aggregate_usage_stats(steps)


def _default_roles_from_steps(steps: list[dict[str, Any]]) -> FlowRoleConfig:
    keys = [str(step.get("step_key") or "") for step in steps]
    if "review" in keys:
        return FlowRoleConfig(gate_step_key="review", producer_step_keys=["writer"], ordered_step_keys=keys)
    if "step_2" in keys and "step_1" in keys:
        return FlowRoleConfig(gate_step_key="step_2", producer_step_keys=["step_1"], ordered_step_keys=keys)
    return FlowRoleConfig(gate_step_key=None, producer_step_keys=keys, ordered_step_keys=keys)


def build_iteration_stats(
    steps: list[dict[str, Any]],
    *,
    flow: FlowDefinition | None = None,
    roles: FlowRoleConfig | None = None,
) -> list[dict[str, Any]]:
    """Group pipeline steps into writer–review cycles for manifest display."""
    if not steps:
        return []

    resolved_roles = roles or (resolve_flow_roles(flow) if flow else _default_roles_from_steps(steps))
    if not resolved_roles.gate_step_key:
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
    for group in group_steps_into_iterations(steps, resolved_roles):
        buffer = group.writer_records + ([group.reviewer_record] if group.reviewer_record else [])
        review_record = group.reviewer_record
        verdict = Verdict.NONE
        if review_record:
            verdict = parse_verdict(_step_content(review_record))
        producer_keys = resolved_roles.producer_step_key_set
        iterations.append(
            {
                "iteration": group.iteration_number,
                "draft_number": sum(
                    1 for entry in buffer if str(entry.get("step_key") or "") in producer_keys
                ),
                "steps": buffer,
                "stats": _aggregate_usage_stats(buffer),
                "verdict": verdict.value if verdict != Verdict.NONE else None,
                "accepted": verdict == Verdict.ACCEPT,
            }
        )

    return iterations


def production_summary(
    steps: list[dict[str, Any]],
    *,
    draft_number: int = 0,
    review_round: int = 0,
    flow: FlowDefinition | None = None,
    roles: FlowRoleConfig | None = None,
) -> dict[str, Any]:
    resolved_roles = roles or (resolve_flow_roles(flow) if flow else _default_roles_from_steps(steps))
    iteration_stats = build_iteration_stats(steps, roles=resolved_roles)
    gate_key = resolved_roles.gate_step_key
    producer_keys = resolved_roles.producer_step_key_set

    draft_count = sum(1 for step in steps if str(step.get("step_key") or "") in producer_keys)
    review_count = sum(1 for step in steps if gate_key and str(step.get("step_key") or "") == gate_key)
    reject_count = sum(
        1
        for step in steps
        if gate_key
        and str(step.get("step_key") or "") == gate_key
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
    flow: FlowDefinition | None = None,
) -> dict[str, Any]:
    data = dict(manifest or {})
    steps = list(data.get("step_stats") or data.get("steps") or [])
    roles = resolve_flow_roles(flow) if flow else None
    iteration_stats = build_iteration_stats(steps, flow=flow, roles=roles)
    production = production_summary(
        steps,
        draft_number=int(data.get("draft_number") or draft_number or 0),
        review_round=int(data.get("review_round") or review_round or 0),
        flow=flow,
        roles=roles,
    )
    data["iteration_stats"] = iteration_stats
    data["production"] = production
    data["draft_number"] = production["draft_number"]
    data["review_round"] = production["review_round"]
    return data
