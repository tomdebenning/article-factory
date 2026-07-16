from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

from article_factory.services.review_parser import review_json_prompt_instructions


class FlowStepLoop(BaseModel):
    enabled: bool = False
    goto_step_id: str | None = None


class FlowStepCompletion(BaseModel):
    can_complete: bool = True
    can_loop: bool = False
    loop_goto_step_id: str | None = None


class FlowStep(BaseModel):
    step_id: str
    order: int = Field(ge=1)
    step_key: str = Field(..., min_length=1, max_length=32)
    label: str = Field(..., min_length=1, max_length=128)
    system_prompt: str = ""
    user_prompt_template: str = ""
    model: str = ""
    puller: str = ""
    loop: FlowStepLoop | None = None
    save_response_to_disk: bool = False
    enabled_tools: dict[str, bool] | None = None
    completion: FlowStepCompletion | None = None


class FlowPerformanceConfig(BaseModel):
    gate_step_key: str | None = None
    producer_step_keys: list[str] = Field(default_factory=list)


class FlowDefinition(BaseModel):
    version: int = 1
    slug: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    max_iterations: int = Field(default=10, ge=1, le=100)
    article_step_id: str | None = None
    performance: FlowPerformanceConfig | None = None
    steps: list[FlowStep] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def validate_flow(self) -> FlowDefinition:
        steps = sorted(self.steps, key=lambda item: item.order)
        if len(steps) != len(self.steps):
            raise ValueError("Duplicate step order values are not allowed")

        step_ids = {step.step_id for step in steps}
        if len(step_ids) != len(steps):
            raise ValueError("Duplicate step_id values are not allowed")

        keys = [step.step_key for step in steps]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate step_key values are not allowed")

        for index, step in enumerate(steps):
            expected_order = index + 1
            if step.order != expected_order:
                raise ValueError(f"Step orders must be contiguous starting at 1 (got {step.order} at index {index})")

        first_id = steps[0].step_id
        last = steps[-1]
        completion = last.completion or FlowStepCompletion()

        if not completion.can_complete and not completion.can_loop:
            raise ValueError("Last step must allow complete and/or loop")

        if completion.can_loop and not completion.loop_goto_step_id:
            raise ValueError("Last step loop requires loop_goto_step_id")

        if completion.loop_goto_step_id and completion.loop_goto_step_id not in step_ids:
            raise ValueError("Last step loop_goto_step_id references a missing step")

        if completion.loop_goto_step_id == last.step_id:
            raise ValueError("Last step cannot loop to itself")

        for step in steps[1:]:
            loop = step.loop
            if not loop or not loop.enabled:
                continue
            if not loop.goto_step_id:
                raise ValueError(f"Step {step.order} loop requires goto_step_id")
            if loop.goto_step_id not in step_ids:
                raise ValueError(f"Step {step.order} loop references a missing step")
            if loop.goto_step_id == step.step_id:
                raise ValueError(f"Step {step.order} cannot loop to itself")
            target_order = next(item.order for item in steps if item.step_id == loop.goto_step_id)
            if target_order >= step.order:
                raise ValueError(f"Step {step.order} must loop back to an earlier step")

        if self.article_step_id and self.article_step_id not in step_ids:
            raise ValueError("article_step_id references a missing step")

        if not self.article_step_id:
            object.__setattr__(self, "article_step_id", first_id)

        return self


def slugify_step_key(label: str, order: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not slug:
        slug = f"step_{order}"
    return slug[:32]


def new_flow_step(*, order: int, label: str | None = None, step_key: str | None = None) -> FlowStep:
    name = label or f"Step {order}"
    key = step_key or slugify_step_key(name, order)
    return FlowStep(
        step_id=str(uuid.uuid4()),
        order=order,
        step_key=key,
        label=name,
        system_prompt="You are a helpful assistant.",
        user_prompt_template="{{topic}}",
    )


def new_flow_definition(*, slug: str, display_name: str, step_count: int) -> FlowDefinition:
    steps = [new_flow_step(order=index + 1) for index in range(step_count)]
    last = steps[-1]
    last.completion = FlowStepCompletion(can_complete=True, can_loop=False)
    if step_count > 1:
        last.system_prompt = (
            "You review work for quality and accuracy. Provide detailed feedback in the body "
            "of your response. End with a final line: VERDICT: ACCEPT or VERDICT: REJECT."
            + review_json_prompt_instructions()
        )
        last.user_prompt_template = (
            "Topic: {{topic}}\n\n"
            "{{feedback}}"
            "Draft:\n{{draft}}\n\n"
            "Review the draft thoroughly, then end with VERDICT: ACCEPT or VERDICT: REJECT."
        )
        last.completion = FlowStepCompletion(
            can_complete=True,
            can_loop=True,
            loop_goto_step_id=steps[0].step_id,
        )
    return FlowDefinition(slug=slug, display_name=display_name, steps=steps)


def flow_to_dict(flow: FlowDefinition) -> dict[str, Any]:
    return flow.model_dump(mode="json")


def flow_from_dict(data: dict[str, Any]) -> FlowDefinition:
    return FlowDefinition.model_validate(data)


def strip_runtime_overrides(flow: FlowDefinition) -> FlowDefinition:
    """Flows define prompts only; model and puller are chosen when a queue starts."""
    steps = []
    for step in flow.steps:
        cleaned = step.model_copy(deep=True)
        cleaned.model = ""
        cleaned.puller = ""
        steps.append(cleaned)
    return flow.model_copy(update={"steps": steps})
