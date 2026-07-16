from __future__ import annotations

from article_factory.services.flow_schema import FlowDefinition, FlowStep


def build_step_variables(
    *,
    topic: str,
    feedback: str,
    step_outputs: dict[str, str],
    steps: list[FlowStep],
    article_step_id: str | None = None,
) -> dict[str, str]:
    variables: dict[str, str] = {
        "topic": topic,
        "feedback": feedback,
    }

    for step in steps:
        content = step_outputs.get(step.step_id) or step_outputs.get(step.step_key) or ""
        variables[step.step_key] = content
        variables[f"step.{step.step_key}"] = content

    draft = ""
    if article_step_id:
        draft = step_outputs.get(article_step_id, "")
    if not draft and steps:
        first = steps[0]
        draft = step_outputs.get(first.step_id) or step_outputs.get(first.step_key) or ""
    if not draft:
        draft = variables.get("writer", "")
    variables["draft"] = draft
    variables.setdefault("sources", variables.get("source_finder", ""))
    variables.setdefault("fact_check", variables.get("fact_asserter", ""))
    return variables


def article_body(flow: FlowDefinition, steps: list[FlowStep], step_outputs: dict[str, str]) -> str:
    target_id = flow.article_step_id
    if target_id:
        body = step_outputs.get(target_id, "")
        if body:
            return body
    if steps:
        first = steps[0]
        return step_outputs.get(first.step_id) or step_outputs.get(first.step_key) or ""
    return ""
