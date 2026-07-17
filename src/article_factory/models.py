from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from article_factory.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FlowQueue(Base):
    """Named topic queue with an assigned flow — multiple queues can run in parallel."""

    __tablename__ = "flow_queues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    flow_path: Mapped[str] = mapped_column(String(256), default="sports/standard-4-step.flow.json")
    flow_version_id: Mapped[int | None] = mapped_column(ForeignKey("flow_versions.id"), nullable=True)
    topic_slug: Mapped[str] = mapped_column(String(64), default="general")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    dispatch_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Persona(Base):
    """Writing style preset — merged into flow step system prompts."""

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    style_prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SavedQueue(Base):
    """Saved queue template — name, flow, model, and topic list for reuse."""

    __tablename__ = "saved_queues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    flow_path: Mapped[str] = mapped_column(String(256))
    topic_slug: Mapped[str] = mapped_column(String(64), default="general")
    default_model: Mapped[str] = mapped_column(String(128), default="")
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class TopicQueueItem(Base):
    __tablename__ = "topic_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_queue_id: Mapped[int | None] = mapped_column(ForeignKey("flow_queues.id"), nullable=True, index=True)
    topic_slug: Mapped[str] = mapped_column(String(64), index=True)
    flow_path: Mapped[str] = mapped_column(String(256), default="sports/standard-4-step.flow.json")
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ShiftPlan(Base):
    """One planned shift window — desks and assignments dispatch while active."""

    __tablename__ = "shift_plans"
    __table_args__ = (UniqueConstraint("window_starts_at", name="uq_shift_plans_window_starts_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_key: Mapped[str] = mapped_column(String(16), index=True)
    window_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    default_model: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShiftDeskSlot(Base):
    """A desk staffed on a shift plan."""

    __tablename__ = "shift_desk_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_plan_id: Mapped[int] = mapped_column(ForeignKey("shift_plans.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    desk_path: Mapped[str] = mapped_column(String(256))
    topic_slug: Mapped[str] = mapped_column(String(64), default="general")
    flow_version_id: Mapped[int | None] = mapped_column(ForeignKey("flow_versions.id"), nullable=True)
    dispatch_order: Mapped[int] = mapped_column(Integer, default=100)
    reporter_selection_mode: Mapped[str] = mapped_column(String(32), default="round_robin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ShiftAssignment(Base):
    """One story assignment for a desk on a shift."""

    __tablename__ = "shift_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_desk_slot_id: Mapped[int] = mapped_column(ForeignKey("shift_desk_slots.id"), index=True)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reporter_persona_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FactoryRun(Base):
    __tablename__ = "factory_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    topic_slug: Mapped[str] = mapped_column(String(64), index=True)
    flow_path: Mapped[str] = mapped_column(String(256), default="sports/standard-4-step.flow.json")
    queue_item_id: Mapped[int | None] = mapped_column(ForeignKey("topic_queue.id"), nullable=True)
    shift_plan_id: Mapped[int | None] = mapped_column(ForeignKey("shift_plans.id"), nullable=True, index=True)
    shift_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift_assignments.id"), nullable=True, index=True
    )
    reporter_persona_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reporter_persona_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    current_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_puller: Mapped[str] = mapped_column(String(128), default="")
    selected_model: Mapped[str] = mapped_column(String(128), default="")
    draft_number: Mapped[int] = mapped_column(Integer, default=1)
    review_round: Mapped[int] = mapped_column(Integer, default=0)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    pipeline_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flow_version_id: Mapped[int | None] = mapped_column(ForeignKey("flow_versions.id"), nullable=True, index=True)
    topic_queue_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic_queue_snapshots.id"), nullable=True, index=True
    )
    first_pass_accept: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class FlowVersion(Base):
    """Immutable snapshot of a flow's prompts and config."""

    __tablename__ = "flow_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_path: Mapped[str] = mapped_column(String(256), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    flow_content: Mapped[dict[str, Any]] = mapped_column(JSON)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TopicQueueSnapshot(Base):
    """Frozen view of a topic queue batch when runs were dispatched."""

    __tablename__ = "topic_queue_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_queue_id: Mapped[int | None] = mapped_column(ForeignKey("flow_queues.id"), nullable=True, index=True)
    queue_slug: Mapped[str] = mapped_column(String(64), default="")
    queue_name: Mapped[str] = mapped_column(String(128), default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    topics: Mapped[list[Any]] = mapped_column(JSON, default=list)
    topic_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunErrorTag(Base):
    """Manual error classification override and note for a factory run."""

    __tablename__ = "run_error_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    error_group: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PromptAnalysis(Base):
    """Stored result of a manual Analyze flow run."""

    __tablename__ = "prompt_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_path: Mapped[str] = mapped_column(String(256), index=True)
    flow_version_id: Mapped[int | None] = mapped_column(ForeignKey("flow_versions.id"), nullable=True)
    topic_queue_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic_queue_snapshots.id"), nullable=True
    )
    selected_model: Mapped[str] = mapped_column(String(128), default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    first_pass_rate: Mapped[float | None] = mapped_column(nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    suggestions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FactorySettings(Base):
    """Singleton row id=1 — runtime integration settings (editable from admin UI)."""

    __tablename__ = "factory_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    control_plane_url: Mapped[str] = mapped_column(String(512), default="")
    cms_url: Mapped[str] = mapped_column(String(512), default="")
    cms_api_key: Mapped[str] = mapped_column(String(256), default="")
    default_puller: Mapped[str] = mapped_column(String(128), default="")
    default_model: Mapped[str] = mapped_column(String(128), default="")
    default_flow_path: Mapped[str] = mapped_column(
        String(256), default="sports/standard-4-step.flow.json"
    )
    factory_api_key: Mapped[str] = mapped_column(String(256), default="")
    brave_search_api_key: Mapped[str] = mapped_column(String(256), default="")
    gateway_id: Mapped[str] = mapped_column(String(128), default="")
    gateway_display_name: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CompletedArticle(Base):
    __tablename__ = "completed_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    queue_item_id: Mapped[int | None] = mapped_column(ForeignKey("topic_queue.id"), nullable=True)
    topic_slug: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, default="")
    body_markdown: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StepExecution(Base):
    """Live trace of a flow step's control-plane task lifecycle."""

    __tablename__ = "step_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    step_key: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    agent_id: Mapped[str] = mapped_column(String(128), default="")
    conversation_id: Mapped[str] = mapped_column(String(64), default="")
    puller: Mapped[str] = mapped_column(String(128), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    cp_queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tools_used: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunTelemetry(Base):
    """Run-level performance and quality telemetry (derived from stored runs)."""

    __tablename__ = "run_telemetry"
    __table_args__ = (
        Index("ix_run_telemetry_flow_path_version", "flow_path", "flow_version_id"),
        Index("ix_run_telemetry_selected_model", "selected_model"),
        Index("ix_run_telemetry_run_status", "run_status"),
        Index("ix_run_telemetry_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    flow_path: Mapped[str] = mapped_column(String(256), index=True)
    flow_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    topic_slug: Mapped[str] = mapped_column(String(64), default="")
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_queue_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_model: Mapped[str] = mapped_column(String(128), default="")
    selected_puller: Mapped[str] = mapped_column(String(128), default="")
    run_status: Mapped[str] = mapped_column(String(32), default="")
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_pass_accept: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    iteration_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    draft_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    highest_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lowest_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_change: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regression_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_progress_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_llm_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wall_clock_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    termination_reason: Mapped[str] = mapped_column(String(32), default="unknown")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    telemetry_warning_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_article_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class IterationTelemetry(Base):
    __tablename__ = "iteration_telemetry"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_number", "iteration_number", name="uq_iteration_telemetry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    iteration_number: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    writer_step_execution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_step_execution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    writer_step_key: Mapped[str] = mapped_column(String(32), default="")
    reviewer_step_key: Mapped[str] = mapped_column(String(32), default="")
    writer_model: Mapped[str] = mapped_column(String(128), default="")
    reviewer_model: Mapped[str] = mapped_column(String(128), default="")
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    total_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    writer_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_change_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fixed_issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    partially_fixed_issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    not_fixed_issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regressed_issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structured_review_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    writer_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CriterionTelemetry(Base):
    __tablename__ = "criterion_telemetry"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "attempt_number",
            "iteration_number",
            "criterion_key",
            name="uq_criterion_telemetry",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    iteration_number: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    criterion_key: Mapped[str] = mapped_column(String(64))
    criterion_label: Mapped[str] = mapped_column(String(128), default="")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewIssueTelemetry(Base):
    __tablename__ = "review_issue_telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    iteration_number: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    issue_number: Mapped[int] = mapped_column(Integer, default=1)
    category: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_loses_points: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PromptImprovementJob(Base):
    """Background job that analyzes telemetry and creates an improved flow version."""

    __tablename__ = "prompt_improvement_jobs"
    __table_args__ = (
        Index("ix_prompt_improvement_jobs_flow_version", "flow_path", "source_flow_version_id"),
        Index("ix_prompt_improvement_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_path: Mapped[str] = mapped_column(String(256), index=True)
    source_flow_version_id: Mapped[int] = mapped_column(ForeignKey("flow_versions.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="step")
    target_step_key: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress_stage: Mapped[str] = mapped_column(String(64), default="")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    selected_model: Mapped[str] = mapped_column(String(128), default="")
    selected_puller: Mapped[str] = mapped_column(String(128), default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    result_flow_version_id: Mapped[int | None] = mapped_column(ForeignKey("flow_versions.id"), nullable=True)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PromptImprovementReport(Base):
    """Stored LLM analysis used to create an improved flow version."""

    __tablename__ = "prompt_improvement_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("prompt_improvement_jobs.id"), index=True)
    flow_path: Mapped[str] = mapped_column(String(256), index=True)
    source_flow_version_id: Mapped[int] = mapped_column(Integer, index=True)
    result_flow_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="step")
    target_step_key: Mapped[str] = mapped_column(String(32), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    actionable_items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    detailed_report: Mapped[str] = mapped_column(Text, default="")
    example_runs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prompt_changes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    selected_model: Mapped[str] = mapped_column(String(128), default="")
    selected_puller: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
