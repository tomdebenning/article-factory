from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from article_factory.cms_client import CmsClient
from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import FactoryRun, Persona
from article_factory.services.flow_paths import resolve_default_flow_path
from article_factory.services.flow_schema import FlowDefinition, FlowStep, FlowStepCompletion
from article_factory.services.flow_storage import read_flow, save_step_response_to_disk
from article_factory.services.flow_versions import resolve_flow_for_run
from article_factory.services.flow_variables import article_body, build_step_variables
from article_factory.services.puller_selection import select_puller_for_model
from article_factory.services.run_control import (
    clear_run_cancel,
    ensure_run_active,
    fail_in_flight_steps,
    is_run_cancelled,
    mark_run_cancelled_in_db,
    request_run_cancel,
    RunCancelledError,
    take_requeue_flow_path,
)
from article_factory.services.run_recovery import save_pipeline_state
from article_factory.services.runtime_settings import RuntimeSettings, load_runtime_settings
from article_factory.services.flow_roles import is_producer_step, resolve_flow_roles
from article_factory.services.persona_selection import merge_persona_style_prompt
from article_factory.services.step_trace import StepTracer
from article_factory.services.review_parser import review_json_prompt_instructions
from article_factory.services.step_tools import is_review_loop_step, resolve_step_tools
from article_factory.services.verdict import Verdict, extract_feedback_body, parse_verdict
from article_factory.workers.base import StepContext
from article_factory.workers.executor import run_step_from_context

logger = logging.getLogger(__name__)


def sorted_steps(flow: FlowDefinition) -> list[FlowStep]:
    return sorted(flow.steps, key=lambda step: step.order)


def step_index_by_id(steps: list[FlowStep], step_id: str) -> int:
    for index, step in enumerate(steps):
        if step.step_id == step_id:
            return index
    raise ValueError(f"Unknown step_id: {step_id}")


def restore_flow_state(run: FactoryRun) -> tuple[dict[str, str], str, list[dict[str, Any]], int, str | None]:
    state = run.pipeline_state or {}
    step_outputs = dict(state.get("step_outputs") or {})
    feedback = str(state.get("feedback") or "")

    # Legacy pipeline_state fields
    if state.get("draft"):
        step_outputs.setdefault("writer", str(state.get("draft")))
    if state.get("sources"):
        step_outputs.setdefault("source_finder", str(state.get("sources")))
    if state.get("fact_check"):
        step_outputs.setdefault("fact_asserter", str(state.get("fact_check")))

    step_records = list(state.get("step_records") or [])
    iteration = int(state.get("iteration") or run.review_round or 0)
    resume_step_id = state.get("current_step_id")
    if not resume_step_id and run.current_step:
        resume_step_id = str(state.get("current_step_key_map", {}).get(run.current_step) or "")
    return step_outputs, feedback, step_records, iteration, resume_step_id


async def execute_flow_pipeline(
    db: Session,
    *,
    run: FactoryRun,
    flow_path: str,
    topic_prompt: str,
    runtime: RuntimeSettings,
    cms: CmsClient | None,
    emit_step_started,
    complete_run,
    resume_from_step_id: str | None = None,
) -> FactoryRun:
    flow = resolve_flow_for_run(db, run)
    steps = sorted_steps(flow)
    roles = resolve_flow_roles(flow)
    cp = ControlPlaneClient(base_url=runtime.control_plane_url)
    run_model = run.selected_model or runtime.default_model
    if not run_model:
        raise RuntimeError("No model configured — select a model when starting a flow")
    run_puller = run.selected_puller or await select_puller_for_model(cp, run_model)
    run.selected_model = run_model
    run.selected_puller = run_puller
    db.commit()
    db.refresh(run)

    step_outputs, feedback, step_records, iteration, stored_step_id = restore_flow_state(run)
    start_index = 0
    resume_id = resume_from_step_id or stored_step_id
    if resume_id:
        try:
            start_index = step_index_by_id(steps, resume_id)
        except ValueError:
            start_index = 0

    while iteration <= flow.max_iterations:
        looped = False
        for index in range(start_index, len(steps)):
            start_index = 0
            step = steps[index]
            is_last = index == len(steps) - 1

            if await is_run_cancelled(run.run_id):
                raise RunCancelledError(f"Run {run.run_id} was stopped")

            await ensure_run_active(db, run)
            run.current_step = step.step_key
            db.commit()
            await emit_step_started(step.step_key)

            variables = build_step_variables(
                topic=topic_prompt,
                feedback=feedback,
                step_outputs=step_outputs,
                steps=steps[:index],
                article_step_id=flow.article_step_id,
            )
            if not variables.get("draft", "").strip() and index > 0:
                variables["draft"] = article_body(flow, steps[:index], step_outputs)
            if not variables.get("draft", "").strip() and index > 0:
                logger.warning(
                    "Run %s step %s prompt has no draft/article body (outputs=%s)",
                    run.run_id,
                    step.step_key,
                    sorted(step_outputs.keys()),
                )
            step_puller = run_puller
            step_model = run_model
            system_prompt = step.system_prompt
            if is_producer_step(step.step_key, roles) and (run.reporter_persona_slug or "").strip():
                persona = db.query(Persona).filter_by(slug=run.reporter_persona_slug.strip()).one_or_none()
                if persona is not None:
                    system_prompt = merge_persona_style_prompt(system_prompt, persona.style_prompt)
            if is_review_loop_step(step):
                system_prompt = f"{system_prompt.rstrip()}{review_json_prompt_instructions()}"
            ctx = StepContext(
                step_key=step.step_key,
                label=step.label,
                system_prompt=system_prompt,
                user_prompt_template=step.user_prompt_template,
                puller=step_puller,
                model=step_model,
                variables=variables,
                enabled_tools=resolve_step_tools(
                    step.enabled_tools,
                    review_step=is_review_loop_step(step),
                ),
                run_id=run.run_id,
                brave_search_api_key=runtime.brave_search_api_key,
            )
            tracer = StepTracer(
                db,
                run_id=run.run_id,
                step_key=step.step_key,
                puller=ctx.puller,
                model=ctx.model,
            )
            record = await run_step_from_context(ctx, cp, run_id=run.run_id, tracer=tracer)
            db.refresh(run)
            if is_producer_step(step.step_key, roles) and (run.reporter_persona_name or "").strip():
                record["persona_name"] = run.reporter_persona_name
                record["persona_slug"] = run.reporter_persona_slug
            if run.status != "running" or await is_run_cancelled(run.run_id):
                raise RunCancelledError(f"Run {run.run_id} was stopped")
            step_records.append(record)

            content = record.get("content") or ""
            step_outputs[step.step_id] = content
            step_outputs[step.step_key] = content

            if step.save_response_to_disk:
                save_step_response_to_disk(
                    run_id=run.run_id,
                    step_order=step.order,
                    step_key=step.step_key,
                    content=content,
                )

            completion = step.completion or FlowStepCompletion()
            if is_last:
                if completion.can_complete and completion.can_loop:
                    verdict = parse_verdict(content)
                    if verdict == Verdict.ACCEPT:
                        draft = article_body(flow, steps, step_outputs)
                        await complete_run(draft, step_records)
                        return run
                    if verdict == Verdict.REJECT:
                        if not completion.loop_goto_step_id:
                            run.status = "failed"
                            run.error = "Last step rejected but no loop target configured"
                            run.finished_at = datetime.now(timezone.utc)
                            db.commit()
                            return run
                        feedback = extract_feedback_body(content)
                        iteration += 1
                        run.review_round = iteration
                        run.draft_number += 1
                        start_index = step_index_by_id(steps, completion.loop_goto_step_id)
                        looped = True
                        save_pipeline_state(
                            db,
                            run,
                            step_outputs=step_outputs,
                            feedback=feedback,
                            step_records=step_records,
                            current_step_id=steps[start_index].step_id,
                            iteration=iteration,
                        )
                        break
                    run.status = "failed"
                    run.error = "Last step response missing VERDICT: ACCEPT or VERDICT: REJECT"
                    run.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    return run

                if completion.can_complete and not completion.can_loop:
                    draft = article_body(flow, steps, step_outputs)
                    await complete_run(draft, step_records)
                    return run

                run.status = "failed"
                run.error = "Last step must allow completion for linear flows"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return run

            loop = step.loop
            if loop and loop.enabled:
                verdict = parse_verdict(content)
                if verdict == Verdict.REJECT and loop.goto_step_id:
                    feedback = extract_feedback_body(content)
                    iteration += 1
                    run.review_round = iteration
                    run.draft_number += 1
                    start_index = step_index_by_id(steps, loop.goto_step_id)
                    looped = True
                    save_pipeline_state(
                        db,
                        run,
                        step_outputs=step_outputs,
                        feedback=feedback,
                        step_records=step_records,
                        current_step_id=steps[start_index].step_id,
                        iteration=iteration,
                    )
                    break

            save_pipeline_state(
                db,
                run,
                step_outputs=step_outputs,
                feedback=feedback,
                step_records=step_records,
                current_step_id=step.step_id,
                iteration=iteration,
            )

        if looped:
            if iteration > flow.max_iterations:
                run.status = "failed"
                run.error = "Max flow iterations exceeded"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return run
            continue
        break

    run.status = "failed"
    run.error = "Flow ended without completion"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return run


def default_flow_path_for_topic(topic_slug: str, db: Session | None = None) -> str:
    """Topic slug no longer selects a flow; use factory default_flow_path instead."""
    del topic_slug
    if db is None:
        from article_factory.db import SessionLocal

        session = SessionLocal()
        try:
            return resolve_default_flow_path(session)
        finally:
            session.close()
    return resolve_default_flow_path(db)
