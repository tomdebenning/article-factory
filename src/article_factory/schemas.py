from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class QueueItemBody(BaseModel):
    topic_slug: str = "general"
    flow_path: str = ""
    flow_queue_id: int | None = None
    prompt: str
    priority: int = 100


class QueueBatchBody(BaseModel):
    topics: list[str] = Field(..., min_length=1)
    topic_slug: str = "general"
    flow_path: str = ""
    flow_queue_id: int | None = None
    priority: int = 100


class FlowQueueBody(BaseModel):
    name: str
    flow_path: str = ""
    topic_slug: str = "general"
    slug: str = ""
    enabled: bool = True


class FlowQueueUpdateBody(BaseModel):
    name: str | None = None
    flow_path: str | None = None
    topic_slug: str | None = None
    enabled: bool | None = None
    dispatch_order: int | None = None


class FlowQueueEnqueueBody(BaseModel):
    topics: list[str] = Field(..., min_length=1)
    priority: int = 100


class QueuePresetBody(BaseModel):
    name: str
    slug: str = ""
    topic_slug: str = "general"
    flow_path: str
    default_model: str = ""
    topics: list[str] = Field(default_factory=list)


class FlowQueueStartBody(BaseModel):
    name: str
    flow_path: str
    flow_version_id: int | None = None
    topic_slug: str = "general"
    default_model: str = ""
    topics: list[str] = Field(default_factory=list)
    save_preset: bool = False
    preset_slug: str = ""
    queue_id: int | None = None
    enabled: bool = True


class SwitchFlowBody(BaseModel):
    flow_path: str
    set_as_default: bool = True
    clear_history: bool = True
    update_queued: bool = False
    requeue_running: bool = False
    start_prompt: str = ""
    topic_slug: str = "general"


class StopAllRunsBody(BaseModel):
    requeue: bool = False
    flow_path: str = ""


class CompletedArticleView(BaseModel):
    id: int
    run_id: str
    queue_item_id: int | None
    topic_slug: str
    title: str
    summary: str
    body_markdown: str
    manifest: dict[str, Any] | None = None
    created_at: datetime | None

    model_config = {"from_attributes": True}


class FactoryStatusView(BaseModel):
    state: str
    active_run_id: str | None = None
    queue_depth: int = 0


class RunSummary(BaseModel):
    run_id: str
    topic_slug: str
    status: str
    current_step: str | None
    selected_puller: str = ""
    selected_model: str = ""
    draft_number: int
    review_round: int
    flow_path: str = "sports/standard-4-step.flow.json"
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None = None
    queue_item_id: int | None = None
    flow_version_id: int | None = None
    topic_queue_snapshot_id: int | None = None
    first_pass_accept: bool | None = None

    model_config = {"from_attributes": True}


class StepExecutionView(BaseModel):
    id: int
    run_id: str
    step_key: str
    status: str
    agent_id: str
    conversation_id: str
    puller: str
    model: str
    cp_queue_depth: int | None = None
    error: str | None = None
    response_content: str | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    pulled_at: str | None = None
    completed_at: str | None = None


class PullerView(BaseModel):
    puller_name: str
    status: str
    supported_models: list[str]
    is_active: bool
    is_stale: bool
    last_heartbeat_at: str | None = None
    current_task: dict[str, Any] | None = None


class FactorySettingsBody(BaseModel):
    control_plane_url: str = Field(..., min_length=1)
    cms_url: str = ""
    cms_api_key: str = ""
    default_puller: str = ""
    default_model: str = ""
    default_flow_path: str = "sports/standard-4-step.flow.json"
    brave_search_api_key: str = ""


class FactorySettingsView(BaseModel):
    control_plane_url: str
    cms_url: str
    cms_api_key: str
    default_puller: str
    default_model: str
    default_flow_path: str
    brave_search_api_key: str = ""
    brave_search_configured: bool = False
    gateway_id: str = ""
    gateway_display_name: str = "Article Factory"
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class FactoryGatewayIdentityBody(BaseModel):
    gateway_display_name: str = Field(..., min_length=1)


class PersonaBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str | None = Field(default=None, max_length=64)
    description: str = ""
    style_prompt: str = Field(..., min_length=1)


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str


class QueueRetryBlocker(BaseModel):
    id: str
    label: str
    message: str
    action_label: str | None = None
    action_path: str | None = None


class QueueRetryResult(BaseModel):
    ok: bool
    message: str
    item: dict[str, Any] | None = None
    blockers: list[QueueRetryBlocker] = Field(default_factory=list)


class ShiftPlanEnsureBody(BaseModel):
    window_key: str


class ShiftDeskSlotBody(BaseModel):
    desk_path: str
    topic_slug: str = "general"
    name: str = ""
    flow_version_id: int | None = None
    reporter_selection_mode: str = "round_robin"


class ShiftAssignmentsBody(BaseModel):
    prompts: list[str] = Field(default_factory=list)
    priority: int = 100


class ShiftPlanSettingsBody(BaseModel):
    default_model: str = ""


class ShiftPlanSaveBody(BaseModel):
    window_key: str
    default_model: str = ""
    desks: list[ShiftDeskSlotBody] = Field(default_factory=list)
    assignments_by_desk_index: dict[str, list[str]] = Field(default_factory=dict)
    locked_by_desk_index: dict[str, list[bool]] = Field(default_factory=dict)
    save_preset: bool = False
    preset_name: str = ""
    preset_slug: str = ""


class StandingOrderBody(BaseModel):
    desk_path: str
    shift_key: str
    topics: list[str] = Field(default_factory=list)
    target_count: int | None = None


class RosterAssignmentUpdate(BaseModel):
    id: int
    prompt: str | None = None
    locked: bool | None = None
    promote_to_manual: bool = False


class RosterReviewBody(BaseModel):
    assignments: list[RosterAssignmentUpdate] = Field(default_factory=list)
