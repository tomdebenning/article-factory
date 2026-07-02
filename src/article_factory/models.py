from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
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


class FactoryRun(Base):
    __tablename__ = "factory_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    topic_slug: Mapped[str] = mapped_column(String(64), index=True)
    flow_path: Mapped[str] = mapped_column(String(256), default="sports/standard-4-step.flow.json")
    queue_item_id: Mapped[int | None] = mapped_column(ForeignKey("topic_queue.id"), nullable=True)
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
