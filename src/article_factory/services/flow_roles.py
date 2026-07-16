"""Resolve writer/producer and reviewer/gate step roles from flow definitions.

All analytics and telemetry modules should use this service instead of
hardcoding step keys such as ``writer`` or ``review``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from article_factory.services.flow_schema import FlowDefinition, FlowStep, FlowStepCompletion


@dataclass(frozen=True)
class FlowRoleConfig:
    gate_step_key: str | None
    producer_step_keys: list[str]
    ordered_step_keys: list[str]

    @property
    def producer_step_key_set(self) -> set[str]:
        return set(self.producer_step_keys)

    @property
    def has_review_loop(self) -> bool:
        return bool(self.gate_step_key)


@dataclass
class IterationGroup:
    iteration_number: int
    writer_records: list[dict[str, Any]]
    reviewer_record: dict[str, Any] | None


def _default_producer_keys(flow: FlowDefinition, gate_key: str) -> list[str]:
    steps = sorted(flow.steps, key=lambda step: step.order)
    gate_order = next((step.order for step in steps if step.step_key == gate_key), len(steps))
    return [step.step_key for step in steps if step.order < gate_order]


def resolve_flow_roles(flow: FlowDefinition) -> FlowRoleConfig:
    """Return gate (reviewer) and producer (writer) step keys for a flow."""
    steps = sorted(flow.steps, key=lambda step: step.order)
    ordered_keys = [step.step_key for step in steps]

    if flow.performance and flow.performance.gate_step_key:
        gate_key = flow.performance.gate_step_key.strip() or None
        producers = list(flow.performance.producer_step_keys or [])
        if gate_key and not producers:
            producers = _default_producer_keys(flow, gate_key)
        return FlowRoleConfig(
            gate_step_key=gate_key,
            producer_step_keys=producers,
            ordered_step_keys=ordered_keys,
        )

    if not steps:
        return FlowRoleConfig(gate_step_key=None, producer_step_keys=[], ordered_step_keys=[])

    last = steps[-1]
    completion = last.completion or FlowStepCompletion()
    if not completion.can_loop:
        return FlowRoleConfig(
            gate_step_key=None,
            producer_step_keys=ordered_keys,
            ordered_step_keys=ordered_keys,
        )

    gate_key = last.step_key
    goto_id = completion.loop_goto_step_id
    if not goto_id:
        producers = [steps[0].step_key]
    else:
        goto_order = next((step.order for step in steps if step.step_id == goto_id), 1)
        gate_order = last.order
        producers = [
            step.step_key
            for step in steps
            if goto_order <= step.order < gate_order
        ]
        if not producers:
            producers = [steps[0].step_key]

    return FlowRoleConfig(
        gate_step_key=gate_key,
        producer_step_keys=producers,
        ordered_step_keys=ordered_keys,
    )


def is_gate_step(step_key: str, roles: FlowRoleConfig) -> bool:
    return bool(roles.gate_step_key) and step_key == roles.gate_step_key


def is_producer_step(step_key: str, roles: FlowRoleConfig) -> bool:
    if roles.gate_step_key and step_key == roles.gate_step_key:
        return False
    if roles.producer_step_keys:
        return step_key in roles.producer_step_key_set
    return True


def resolve_gate_config(flow: FlowDefinition) -> tuple[str | None, list[str]]:
    """Backwards-compatible wrapper used by flow_performance and prompt_coach."""
    roles = resolve_flow_roles(flow)
    return roles.gate_step_key, roles.producer_step_keys


def group_steps_into_iterations(
    step_records: list[dict[str, Any]],
    roles: FlowRoleConfig,
) -> list[IterationGroup]:
    """Group ordered step records into writer-then-reviewer cycles."""
    if not step_records:
        return []

    if not roles.gate_step_key:
        return [
            IterationGroup(
                iteration_number=1,
                writer_records=list(step_records),
                reviewer_record=None,
            )
        ]

    iterations: list[IterationGroup] = []
    buffer: list[dict[str, Any]] = []
    iteration_num = 0

    for record in step_records:
        step_key = str(record.get("step_key") or "")
        if is_gate_step(step_key, roles):
            iteration_num += 1
            writer_records = [entry for entry in buffer if is_producer_step(str(entry.get("step_key") or ""), roles)]
            iterations.append(
                IterationGroup(
                    iteration_number=iteration_num,
                    writer_records=writer_records,
                    reviewer_record=record,
                )
            )
            buffer = []
            continue
        if is_producer_step(step_key, roles):
            buffer.append(record)
        else:
            buffer.append(record)

    if buffer:
        iteration_num += 1
        writer_records = [entry for entry in buffer if is_producer_step(str(entry.get("step_key") or ""), roles)]
        iterations.append(
            IterationGroup(
                iteration_number=iteration_num,
                writer_records=writer_records,
                reviewer_record=None,
            )
        )

    return iterations


def gate_step_keys_from_flow(flow: FlowDefinition) -> set[str]:
    roles = resolve_flow_roles(flow)
    return {roles.gate_step_key} if roles.gate_step_key else set()


def producer_step_keys_from_flow(flow: FlowDefinition) -> set[str]:
    return resolve_flow_roles(flow).producer_step_key_set
