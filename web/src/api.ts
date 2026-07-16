const KEY = "factory_api_key";
const API_KEY_COOKIE = "factory_api_key";
const API_KEY_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

function syncApiKeyCookie(value: string) {
  if (typeof document === "undefined") {
    return;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    document.cookie = `${API_KEY_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
    return;
  }
  document.cookie = `${API_KEY_COOKIE}=${encodeURIComponent(trimmed)}; path=/; max-age=${API_KEY_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`;
}

export function getApiKey(): string {
  return localStorage.getItem(KEY) || "";
}

export function setApiKey(value: string) {
  localStorage.setItem(KEY, value);
  syncApiKeyCookie(value);
}

if (typeof window !== "undefined") {
  syncApiKeyCookie(getApiKey());
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) {
    return response.statusText || `Request failed (${response.status})`;
  }
  try {
    const data = JSON.parse(text) as { detail?: unknown };
    if (typeof data.detail === "string") {
      return data.detail;
    }
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((item) => {
          if (typeof item === "string") {
            return item;
          }
          if (item && typeof item === "object" && "msg" in item) {
            const entry = item as { loc?: unknown[]; msg?: string };
            const path = Array.isArray(entry.loc) ? entry.loc.join(".") : "field";
            return `${path}: ${entry.msg ?? "invalid"}`;
          }
          return String(item);
        })
        .join("; ");
    }
  } catch {
    /* plain text error body */
  }
  return text;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": getApiKey(),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

export type FactorySettings = {
  control_plane_url: string;
  cms_url: string;
  cms_api_key: string;
  default_puller: string;
  default_model: string;
  default_flow_path: string;
  brave_search_api_key: string;
  brave_search_configured: boolean;
  gateway_id: string;
  gateway_display_name: string;
  updated_at?: string | null;
};

export type FlowTemplate = {
  path: string;
  display_name: string;
  slug: string;
  step_count: number;
  modified_at?: string;
};

export type PullerInfo = {
  puller_name: string;
  status: string;
  supported_models: string[];
  is_active: boolean;
  is_stale: boolean;
  last_heartbeat_at?: string | null;
};

export type StepUsage = {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
};

export type ToolUseEntry = {
  tool: string;
  label?: string;
  detail?: string;
  round?: number;
  ok?: boolean;
};

export type StepProgress = {
  activity?: string;
  cp_round?: number;
  updated_at?: string;
};

export type FlowStepSummary = {
  step_key: string;
  label: string;
  order: number;
};

export type StepExecution = {
  id: number;
  run_id: string;
  step_key: string;
  status: string;
  agent_id: string;
  conversation_id: string;
  puller: string;
  model: string;
  cp_queue_depth?: number | null;
  error?: string | null;
  response_content?: string | null;
  duration_ms?: number | null;
  usage?: StepUsage | null;
  tools_used?: ToolUseEntry[];
  progress?: StepProgress;
  turns?: number | null;
  started_at?: string | null;
  submitted_at?: string | null;
  pulled_at?: string | null;
  completed_at?: string | null;
};

export type RunSummary = {
  run_id: string;
  topic_slug: string;
  flow_path?: string;
  status: string;
  current_step: string | null;
  selected_puller?: string;
  selected_model?: string;
  draft_number: number;
  review_round: number;
  error?: string | null;
  topic_prompt?: string | null;
  flow_queue_id?: number | null;
  flow_queue_name?: string | null;
  queue_item_id?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  flow_version_id?: number | null;
  topic_queue_snapshot_id?: number | null;
  first_pass_accept?: boolean | null;
  flow_version_number?: number | null;
  flow_version_message?: string | null;
  topic_queue_label?: string | null;
  flow_steps?: FlowStepSummary[];
  steps?: StepExecution[];
};

export type RunningGroup = {
  queue_id: number | null;
  queue_name: string;
  queue_slug: string | null;
  flow_path: string;
  model: string;
  running_count: number;
  queued_count: number;
  runs: RunSummary[];
};

export type ActiveOverview = {
  running_groups: RunningGroup[];
  history_runs: RunSummary[];
};

export type RunStepFile = {
  name: string;
  path: string;
  size_bytes: number;
};

export type RunDetail = {
  run: RunSummary;
  steps: StepExecution[];
  step_files?: RunStepFile[];
};

export type FlowQueueCounts = {
  queued: number;
  running: number;
  completed: number;
  failed: number;
};

export type FlowQueueSummary = {
  id: number;
  slug: string;
  name: string;
  flow_path: string;
  topic_slug: string;
  enabled: boolean;
  dispatch_order: number;
  created_at: string | null;
  counts: FlowQueueCounts;
  active_run_id: string | null;
};

export type Persona = {
  slug: string;
  name: string;
  description: string;
  style_prompt: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type QueuePresetSummary = {
  slug: string;
  name: string;
  topic_slug: string;
  flow_path: string;
  default_model: string;
  topic_count: number;
  updated_at?: string | null;
};

export type QueuePreset = QueuePresetSummary & {
  version: number;
  topics: string[];
  path?: string;
};

export type FlowQueueStartResult = {
  ok: boolean;
  queue: FlowQueueSummary;
  enqueued: number;
  preset: QueuePreset | null;
  message: string;
};

export type QueueItem = {
  id: number;
  flow_queue_id?: number | null;
  flow_queue_name?: string | null;
  topic_slug: string;
  flow_path?: string;
  prompt: string;
  status: string;
  priority?: number;
  created_at?: string | null;
  run_id?: string | null;
  run_status?: string | null;
  run_error?: string | null;
  current_step?: string | null;
  steps?: StepExecution[];
  rerunnable?: boolean;
};

export type QueueRetryBlocker = {
  id: string;
  label: string;
  message: string;
  action_label?: string;
  action_path?: string;
};

export type QueueRetryResult = {
  ok: boolean;
  message: string;
  item?: QueueItem | null;
  blockers?: QueueRetryBlocker[];
};

export type QueueRetryStatus = QueueRetryResult & {
  can_retry: boolean;
  item_status: string;
  run_error?: string | null;
  retriable: boolean;
};

export type WorkspaceFileSummary = {
  path: string;
  filename: string;
  content_type: string;
  size_bytes: number;
};

export type CompletedArticle = {
  id: number;
  run_id: string;
  queue_item_id: number | null;
  topic_slug: string;
  title: string;
  summary: string;
  body_markdown: string;
  has_content?: boolean;
  run_exists?: boolean;
  run_status?: string | null;
  step_files?: RunStepFile[];
  workspace_files?: WorkspaceFileSummary[];
  model?: string;
  stats?: {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    llm_calls?: number;
    total_duration_ms?: number;
  };
  manifest?: {
    selected_model?: string;
    stats?: StepUsage & {
      llm_calls?: number;
      total_duration_ms?: number;
      input_tokens?: number;
      output_tokens?: number;
      total_tokens?: number;
    };
    steps?: Array<Record<string, unknown>>;
    step_stats?: Array<Record<string, unknown>>;
  } | null;
  created_at?: string | null;
};

export type ReadinessCheck = {
  id: string;
  label: string;
  ok: boolean;
  message: string;
  action_label?: string;
  action_path?: string;
};

export type DurationStats = {
  count: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  median_duration_ms: number;
};

export type FactoryStepStatRow = {
  step_execution_id: number;
  run_id: string;
  step_key: string;
  puller: string;
  model: string;
  duration_ms: number;
  turns?: number | null;
  prompt: string;
  topic_slug: string;
  flow_path?: string;
  completed_at?: string | null;
};

export type FactoryStats = {
  summary: DurationStats;
  by_puller: Array<DurationStats & { puller: string }>;
  by_model: Array<DurationStats & { model: string }>;
  by_step: Array<DurationStats & { step_key: string }>;
  by_puller_step: Array<DurationStats & { puller: string; step_key: string }>;
  by_model_step: Array<DurationStats & { model: string; step_key: string }>;
  recent_steps: FactoryStepStatRow[];
};

export type FactoryReadiness = {
  setup_complete: boolean;
  can_write: boolean;
  phase: "setup_required" | "needs_topics" | "ready" | "processing";
  headline: string;
  summary: string;
  next_action: { label: string; path: string };
  checks: ReadinessCheck[];
  issue_checks: ReadinessCheck[];
  available_models: string[];
  active_puller_count: number;
};

export type FactoryStatus = {
  loop_running: boolean;
  state: "idle" | "processing";
  default_model: string;
  default_puller: string;
  control_plane_url: string;
  queue_depth: number;
  queue_counts: {
    queued: number;
    running: number;
    completed: number;
    failed: number;
  };
  readiness: FactoryReadiness;
  active_run: RunSummary | null;
  active_runs: RunSummary[];
  flow_queues?: FlowQueueSummary[];
};

export type SwitchFlowResult = {
  ok: boolean;
  flow_path: string;
  set_as_default: boolean;
  clear_history: boolean;
  cleared?: {
    cleared_queue_items: number;
    deleted_runs: number;
    running_runs_left: number;
  } | null;
  updated_queued_items: number;
  stopped_runs: number;
  stopped_run_ids: string[];
  queued_item_id: number | null;
  needs_start_prompt: boolean;
  message: string;
};

export type StopAllRunsResult = {
  ok: boolean;
  stopped: number;
  run_ids: string[];
  message: string;
};

export type StopAndClearFlowQueueResult = {
  ok: boolean;
  queue_id: number;
  queue_name: string;
  stopped_runs: number;
  stopped_run_ids: string[];
  cleared_queued_items: number;
  cleared_pending_items?: number;
  deleted_runs?: number;
  message: string;
};

export type ConnectionTestResult = {
  ok: boolean;
  message: string;
};

export type AuthKeyStatus = {
  configured: boolean;
  masked: string | null;
};

export type GeneratedAuthKey = {
  api_key: string;
  configured: boolean;
  message: string;
};

export const QUEUE_STATUS_LABEL: Record<string, string> = {
  queued: "Not started",
  running: "Processing",
  completed: "Done",
  failed: "Failed",
};

export type FlowStepLoop = {
  enabled: boolean;
  goto_step_id?: string | null;
};

export type FlowStepCompletion = {
  can_complete: boolean;
  can_loop: boolean;
  loop_goto_step_id?: string | null;
};

export type FlowStep = {
  step_id: string;
  order: number;
  step_key: string;
  label: string;
  system_prompt: string;
  user_prompt_template: string;
  /** Legacy field — model is chosen on Start flows, not in the flow file. */
  model?: string;
  /** Legacy field — puller is assigned automatically at run time. */
  puller?: string;
  loop?: FlowStepLoop | null;
  save_response_to_disk: boolean;
  enabled_tools?: Record<string, boolean> | null;
  completion?: FlowStepCompletion | null;
};

export type FlowDefinition = {
  version: number;
  slug: string;
  display_name: string;
  max_iterations: number;
  article_step_id?: string | null;
  performance?: {
    gate_step_key?: string | null;
    producer_step_keys?: string[];
  } | null;
  steps: FlowStep[];
};

export type FlowVersionSummary = {
  id: number;
  flow_path: string;
  version_number: number;
  content_hash: string;
  message: string;
  created_at?: string | null;
  display_name?: string | null;
  step_count?: number;
  changes_from_previous?: Array<{
    step_key: string;
    change: string;
    field?: string;
    label?: string;
  }>;
};

export type TopicQueueSnapshotSummary = {
  id: number;
  flow_queue_id?: number | null;
  queue_slug: string;
  queue_name: string;
  topic_count: number;
  content_hash: string;
  topics: Array<{ id: number; topic_slug: string; prompt: string; status: string }>;
  created_at?: string | null;
};

export type TurnCountRow = {
  turn: number;
  count: number;
};

export type TurnOutcomeCharts = {
  success_by_turn: TurnCountRow[];
  failure_by_turn: TurnCountRow[];
  success_total: number;
  failure_total: number;
};

export type FlowPerformanceAggregate = {
  run_count: number;
  completed_count: number;
  completion_rate?: number | null;
  first_pass_count: number;
  first_pass_yield_rate?: number | null;
  first_pass_completed_rate?: number | null;
  /** @deprecated use first_pass_completed_rate */
  first_pass_scored_count?: number;
  first_pass_rate: number | null;
  avg_tokens: number | null;
  avg_review_rounds?: number | null;
  median_review_rounds?: number | null;
  avg_step_turns?: number | null;
  median_step_turns?: number | null;
  failure_count?: number;
  failure_rate?: number | null;
  error_groups?: ErrorGroupCount[];
  turn_charts?: TurnOutcomeCharts;
};

export type ErrorGroupCount = {
  error_group: string;
  error_group_label: string;
  count: number;
};

export type ErrorGroupOption = {
  error_group: string;
  error_group_label: string;
};

export type FlowPerformanceRun = {
  run_id: string;
  topic_slug: string;
  status: string;
  flow_version_id?: number | null;
  topic_queue_snapshot_id?: number | null;
  selected_model?: string;
  first_pass_accept?: boolean | null;
  draft_number?: number;
  review_round?: number;
  review_rounds?: number;
  review_cycles?: number;
  total_step_turns?: number;
  iteration_count?: number;
  error_group?: string;
  error_group_label?: string;
  auto_error_group?: string;
  manual_tag?: string | null;
  manual_note?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type FlowPerformanceBatchRow = FlowPerformanceAggregate & {
  topic_queue_snapshot_id?: number | null;
  queue_name?: string | null;
};

export type FlowPerformanceData = {
  flow_path: string;
  overall: FlowPerformanceAggregate;
  by_version: Array<FlowPerformanceAggregate & { flow_version_id?: number | null }>;
  by_topic_queue: Array<FlowPerformanceBatchRow>;
  by_model: Array<FlowPerformanceAggregate & { model: string }>;
  batches: FlowPerformanceBatchRow[];
  runs: FlowPerformanceRun[];
};

export type BatchComparisonTopicRow = {
  queue_item_id?: number | null;
  topic_slug: string;
  prompt_preview?: string;
  run_id?: string | null;
  status: string;
  error_group: string;
  error_group_label: string;
  auto_error_group?: string;
  error_message?: string | null;
  manual_tag?: string | null;
  manual_note?: string | null;
  review_rounds?: number | null;
  review_cycles?: number | null;
  total_step_turns?: number | null;
  step_turns_by_step?: Record<string, number>;
  first_pass_accept?: boolean | null;
  selected_model?: string | null;
  selected_puller?: string | null;
  flow_version_id?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type BatchComparisonData = {
  snapshot: TopicQueueSnapshotSummary;
  flow_path?: string | null;
  filters: {
    topic_queue_snapshot_id: number;
    flow_version_id?: number | null;
    selected_model?: string | null;
    selected_puller?: string | null;
  };
  summary: FlowPerformanceAggregate;
  error_groups: Array<ErrorGroupCount & { run_ids?: string[] }>;
  turn_charts?: TurnOutcomeCharts;
  topics: BatchComparisonTopicRow[];
  runs: BatchComparisonTopicRow[];
};

export type PromptAnalysisResult = {
  id: number;
  flow_path: string;
  flow_version_id?: number | null;
  topic_queue_snapshot_id?: number | null;
  selected_model?: string;
  run_count: number;
  first_pass_rate?: number | null;
  summary: string;
  suggestions: Array<{
    step_key: string;
    diagnosis: string;
    suggestion: string;
    evidence: string[];
  }>;
  created_at?: string | null;
};

export type PromptImprovementStep = {
  step_key: string;
  label: string;
  has_system_prompt: boolean;
  has_user_prompt_template: boolean;
};

export type PromptImprovementJob = {
  id: number;
  flow_path: string;
  source_flow_version_id: number;
  scope: "step" | "flow";
  target_step_key: string;
  status: "queued" | "running" | "completed" | "failed";
  progress_stage: string;
  progress_percent: number;
  selected_model: string;
  selected_puller: string;
  run_count: number;
  result_flow_version_id?: number | null;
  report_id?: number | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
};

export type PromptImprovementReport = {
  id: number;
  job_id: number;
  flow_path: string;
  source_flow_version_id: number;
  result_flow_version_id?: number | null;
  scope: "step" | "flow";
  target_step_key: string;
  summary: string;
  actionable_items: Array<{
    title: string;
    priority?: string;
    rationale?: string;
    evidence_run_ids?: string[];
  }>;
  detailed_report: string;
  prompt_changes: Array<{
    step_key: string;
    label?: string;
    fields?: string[];
    rationale?: string;
    conclusion?: string;
    evidence_run_ids?: string[];
  }>;
  example_runs: {
    success?: Array<Record<string, unknown>>;
    failure?: Array<Record<string, unknown>>;
  };
  selected_model: string;
  selected_puller: string;
  created_at?: string | null;
};

export type FlowTreeNode = {
  name: string;
  path: string;
  type: "folder" | "file";
  modified_at?: string;
  size_bytes?: number;
  children?: FlowTreeNode[];
};

export const DEFAULT_FLOW_PATH = "sports/standard-4-step.flow.json";

function submitTelemetryExportForm(path: string, flowVersionId: number): void {
  const form = document.createElement("form");
  form.method = "GET";
  form.action = "/api/flows/telemetry/export";
  form.target = "_blank";
  form.rel = "noopener noreferrer";
  form.style.display = "none";

  for (const [name, value] of Object.entries({
    path,
    flow_version_id: String(flowVersionId),
  })) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }

  document.body.appendChild(form);
  form.submit();
  form.remove();
}

export const api = {
  authStatus: () => request<AuthKeyStatus>("/api/auth"),
  generateAuthKey: () =>
    request<GeneratedAuthKey>("/api/auth/generate", { method: "POST" }),
  factoryStatus: () => request<FactoryStatus>("/api/factory/status"),
  activeOverview: (historyLimit = 250) =>
    request<ActiveOverview>(`/api/active/overview?history_limit=${historyLimit}`),
  stopAllRuns: (body?: { requeue?: boolean; flow_path?: string }) =>
    request<StopAllRunsResult>("/api/factory/stop-all-runs", {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  switchFlow: (body: {
    flow_path: string;
    set_as_default?: boolean;
    clear_history?: boolean;
    update_queued?: boolean;
    requeue_running?: boolean;
    start_prompt?: string;
    topic_slug?: string;
  }) =>
    request<SwitchFlowResult>("/api/factory/switch-flow", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getSettings: () => request<FactorySettings>("/api/settings"),
  saveSettings: (body: FactorySettings) =>
    request<FactorySettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  updateFactoryIdentity: (gateway_display_name: string) =>
    request<FactorySettings>("/api/settings/gateway-identity", {
      method: "PUT",
      body: JSON.stringify({ gateway_display_name }),
    }),
  testControlPlane: (body?: FactorySettings) =>
    request<ConnectionTestResult>("/api/settings/test/control-plane", {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  testCms: (body?: FactorySettings) =>
    request<ConnectionTestResult>("/api/settings/test/cms", {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  testBraveSearch: (body?: FactorySettings) =>
    request<ConnectionTestResult>("/api/settings/test/brave-search", {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  listPullers: () => request<{ pullers: PullerInfo[] }>("/api/control-plane/pullers"),
  getRun: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  getRunStepFile: (runId: string, filename: string) =>
    request<{ run_id: string; filename: string; content: string }>(
      `/api/runs/${runId}/step-files/${encodeURIComponent(filename)}`,
    ),
  stopRun: (runId: string) =>
    request<{ ok: boolean; message: string; run?: RunSummary }>(`/api/runs/${runId}/stop`, {
      method: "POST",
    }),
  deleteRun: (runId: string) =>
    request<{ ok: boolean; deleted_run_id: string }>(`/api/runs/${runId}`, { method: "DELETE" }),
  publishRun: (runId: string) =>
    request<{ ok: boolean; message?: string; run?: RunSummary }>(`/api/runs/${runId}/publish`, {
      method: "POST",
    }),
  listQueue: () => request<{ items: QueueItem[] }>("/api/queue"),
  getQueueRetryStatus: (itemId: number) =>
    request<QueueRetryStatus>(`/api/queue/${itemId}/retry-status`),
  retryQueueItem: (itemId: number) =>
    request<QueueRetryResult>(`/api/queue/${itemId}/retry`, { method: "POST" }),
  enqueue: (topic_slug: string, prompt: string, flow_path = "") =>
    request<{ id: number; status: string }>("/api/queue", {
      method: "POST",
      body: JSON.stringify({ topic_slug, prompt, flow_path }),
    }),
  enqueueBatch: (topics: string[], topic_slug = "general", flow_path = "", flow_queue_id?: number) =>
    request<{ count: number; items: { id: number; prompt: string; status: string }[] }>(
      "/api/queue/batch",
      {
        method: "POST",
        body: JSON.stringify({ topics, topic_slug, flow_path, flow_queue_id }),
      },
    ),
  listFlowQueues: () => request<{ queues: FlowQueueSummary[] }>("/api/flow-queues"),
  createFlowQueue: (body: { name: string; flow_path?: string; topic_slug?: string; slug?: string }) =>
    request<{ queue: FlowQueueSummary }>("/api/flow-queues", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateFlowQueue: (
    queueId: number,
    body: Partial<Pick<FlowQueueSummary, "name" | "flow_path" | "topic_slug" | "enabled" | "dispatch_order">>,
  ) =>
    request<{ queue: FlowQueueSummary }>(`/api/flow-queues/${queueId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteFlowQueue: (queueId: number) =>
    request<{ ok: boolean; deleted: FlowQueueSummary; deleted_items: number }>(
      `/api/flow-queues/${queueId}`,
      { method: "DELETE" },
    ),
  stopAndClearFlowQueue: (queueId: number) =>
    request<StopAndClearFlowQueueResult>(`/api/flow-queues/${queueId}/stop-and-clear`, {
      method: "POST",
    }),
  enqueueFlowQueueTopics: (queueId: number, topics: string[], priority = 100) =>
    request<{ count: number; items: QueueItem[] }>(`/api/flow-queues/${queueId}/enqueue`, {
      method: "POST",
      body: JSON.stringify({ topics, priority }),
    }),
  listQueuePresets: () => request<{ presets: QueuePresetSummary[] }>("/api/flow-queues/presets"),
  getQueuePreset: (slug: string) => request<{ preset: QueuePreset }>(`/api/flow-queues/presets/${slug}`),
  saveQueuePreset: (body: {
    name: string;
    slug?: string;
    topic_slug?: string;
    flow_path: string;
    default_model?: string;
    topics: string[];
  }) =>
    request<{ preset: QueuePreset }>("/api/flow-queues/presets", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteQueuePreset: (slug: string) =>
    request<{ ok: boolean; slug: string; name: string }>(`/api/flow-queues/presets/${slug}`, {
      method: "DELETE",
    }),
  startFlowQueue: (body: {
    name: string;
    flow_path: string;
    flow_version_id?: number;
    topic_slug?: string;
    default_model: string;
    topics: string[];
    save_preset?: boolean;
    preset_slug?: string;
    queue_id?: number | null;
    enabled?: boolean;
  }) =>
    request<FlowQueueStartResult>("/api/flow-queues/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getFlowTree: (path = "") =>
    request<FlowTreeNode>(`/api/flows/tree${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  getFlow: (path: string) =>
    request<{ path: string; flow: FlowDefinition }>(`/api/flows/file?path=${encodeURIComponent(path)}`),
  saveFlow: (path: string, flow: FlowDefinition) =>
    request<{ path: string; flow: FlowDefinition }>(`/api/flows/file?path=${encodeURIComponent(path)}`, {
      method: "PUT",
      body: JSON.stringify({ flow }),
    }),
  createFlow: (body: { folder: string; slug: string; display_name: string; step_count: number }) =>
    request<{ path: string; flow: FlowDefinition }>("/api/flows/create", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createFlowFolder: (path: string) =>
    request<{ path: string }>("/api/flows/folders", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  deleteFlow: (path: string) =>
    request<{ ok: boolean }>(`/api/flows/file?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
  duplicateFlow: (path: string, slug?: string, display_name?: string) =>
    request<{ path: string; flow: FlowDefinition }>("/api/flows/duplicate", {
      method: "POST",
      body: JSON.stringify({ path, slug, display_name }),
    }),
  moveFlow: (body: { path: string; folder?: string; slug?: string }) =>
    request<{ path: string; flow: FlowDefinition; moved_from: string }>("/api/flows/move", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listFlows: (path = "") =>
    request<{ flows: Array<{ path: string; display_name: string; slug: string; step_count: number; modified_at?: string }> }>(
      `/api/flows/list${path ? `?path=${encodeURIComponent(path)}` : ""}`,
    ),
  deleteFlowFolder: (path: string) =>
    request<{ ok: boolean }>(`/api/flows/folders?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
  listFlowTemplates: () => request<{ templates: FlowTemplate[] }>("/api/flows/templates"),
  createFlowFromTemplate: (body: {
    template_path: string;
    folder: string;
    slug: string;
    display_name: string;
  }) =>
    request<{ path: string; flow: FlowDefinition }>("/api/flows/from-template", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  exportFlow: (path: string) =>
    request<{ path: string; flow: FlowDefinition }>(`/api/flows/export?path=${encodeURIComponent(path)}`),
  importFlow: (body: { folder: string; slug?: string; flow: FlowDefinition; overwrite?: boolean }) =>
    request<{ path: string; flow: FlowDefinition }>("/api/flows/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createFlowVersion: (path: string, message = "") =>
    request<{ version: FlowVersionSummary }>("/api/flows/versions", {
      method: "POST",
      body: JSON.stringify({ path, message }),
    }),
  getFlowVersionDetail: (versionId: number) =>
    request<{ version: FlowVersionSummary & { flow_content: FlowDefinition } }>(
      `/api/flows/versions/detail?version_id=${versionId}`,
    ),
  applyFlowVersion: (versionId: number) =>
    request<{ version: FlowVersionSummary; message: string }>("/api/flows/versions/apply", {
      method: "POST",
      body: JSON.stringify({ version_id: versionId }),
    }),
  listFlowVersions: (path: string) =>
    request<{ flow_path: string; versions: FlowVersionSummary[] }>(
      `/api/flows/versions?path=${encodeURIComponent(path)}`,
    ),
  getFlowPerformance: (
    path: string,
    filters?: { flow_version_id?: number; topic_queue_snapshot_id?: number; selected_model?: string },
  ) => {
    const params = new URLSearchParams({ path });
    if (filters?.flow_version_id) params.set("flow_version_id", String(filters.flow_version_id));
    if (filters?.topic_queue_snapshot_id) {
      params.set("topic_queue_snapshot_id", String(filters.topic_queue_snapshot_id));
    }
    if (filters?.selected_model) params.set("selected_model", filters.selected_model);
    return request<FlowPerformanceData>(`/api/flows/performance?${params.toString()}`);
  },
  downloadTelemetryCsv: async (path: string, flowVersionId: number) => {
    const apiKey = getApiKey().trim();
    if (!apiKey) {
      throw new Error(
        'No factory API key in this browser. Open Settings, paste your key under "Use this key in this browser", then try again.',
      );
    }

    syncApiKeyCookie(apiKey);

    const checkParams = new URLSearchParams({
      path,
      flow_version_id: String(flowVersionId),
      limit: "1",
    });
    const check = await fetch(`/api/flows/telemetry?${checkParams.toString()}`, {
      headers: { "X-API-Key": apiKey },
      credentials: "same-origin",
    });
    if (!check.ok) {
      throw new Error(await readErrorMessage(check));
    }

    submitTelemetryExportForm(path, flowVersionId);
  },
  listFlowTopicQueues: (path: string) =>
    request<{ topic_queues: TopicQueueSnapshotSummary[] }>(
      `/api/flows/topic-queues?path=${encodeURIComponent(path)}`,
    ),
  analyzeFlow: (body: {
    path: string;
    flow_version_id?: number;
    topic_queue_snapshot_id?: number;
    selected_model?: string;
  }) =>
    request<{ analysis: PromptAnalysisResult }>("/api/flows/analyze", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getPromptImprovementSteps: (path: string, flowVersionId: number) =>
    request<{
      flow_path: string;
      flow_version_id: number;
      min_completed_runs: number;
      steps: PromptImprovementStep[];
    }>(
      `/api/flows/prompt-improvement/steps?path=${encodeURIComponent(path)}&flow_version_id=${flowVersionId}`,
    ),
  startPromptImprovement: (body: {
    path: string;
    flow_version_id: number;
    scope: "step" | "flow";
    target_step_key?: string;
    selected_model: string;
    selected_puller: string;
  }) =>
    request<{ job: PromptImprovementJob }>("/api/flows/prompt-improvement", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listPromptImprovementJobs: (path: string, flowVersionId?: number) => {
    const params = new URLSearchParams({ path });
    if (flowVersionId !== undefined) params.set("flow_version_id", String(flowVersionId));
    return request<{ jobs: PromptImprovementJob[] }>(`/api/flows/prompt-improvement?${params.toString()}`);
  },
  getPromptImprovementJob: (jobId: number) =>
    request<{ job: PromptImprovementJob }>(`/api/flows/prompt-improvement/${jobId}`),
  getPromptImprovementReport: (reportId: number) =>
    request<{ report: PromptImprovementReport }>(`/api/flows/prompt-improvement/reports/${reportId}`),
  getBatchComparison: (
    topicQueueSnapshotId: number,
    filters?: {
      flow_version_id?: number;
      selected_model?: string;
      selected_puller?: string;
    },
  ) => {
    const params = new URLSearchParams({
      topic_queue_snapshot_id: String(topicQueueSnapshotId),
    });
    if (filters?.flow_version_id) params.set("flow_version_id", String(filters.flow_version_id));
    if (filters?.selected_model) params.set("selected_model", filters.selected_model);
    if (filters?.selected_puller) params.set("selected_puller", filters.selected_puller);
    return request<BatchComparisonData>(`/api/flows/batch-comparison?${params.toString()}`);
  },
  listErrorGroups: () =>
    request<{ error_groups: ErrorGroupOption[] }>("/api/flows/error-groups"),
  saveRunErrorTag: (runId: string, body: { error_group?: string; note?: string }) =>
    request<{ error_tag: { run_id: string; error_group: string; note: string } }>(
      `/api/flows/runs/${encodeURIComponent(runId)}/error-tag`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  listRuns: () => request<{ runs: RunSummary[] }>("/api/runs"),
  getStats: (recentLimit = 50) =>
    request<FactoryStats>(`/api/stats?recent_limit=${recentLimit}`),
  listArticles: () => request<{ articles: CompletedArticle[] }>("/api/articles"),
  getArticle: (runId: string) => request<{ article: CompletedArticle }>(`/api/articles/${runId}`),
  getArticleStepFile: (runId: string, filename: string) =>
    request<{ run_id: string; filename: string; content: string }>(
      `/api/articles/${runId}/step-files/${encodeURIComponent(filename)}`,
    ),
  getArticleWorkspaceFile: (runId: string, filePath: string) =>
    request<{ run_id: string; path: string; filename: string; content: string; content_type: string }>(
      `/api/articles/${runId}/workspace-files/${encodeURIComponent(filePath)}`,
    ),
  listPersonas: () => request<{ personas: Persona[] }>("/api/personas"),
  getPersona: (slug: string) => request<{ persona: Persona }>(`/api/personas/${encodeURIComponent(slug)}`),
  createPersona: (body: Pick<Persona, "name" | "description" | "style_prompt"> & { slug?: string }) =>
    request<{ persona: Persona }>("/api/personas", { method: "POST", body: JSON.stringify(body) }),
  updatePersona: (
    slug: string,
    body: Pick<Persona, "name" | "description" | "style_prompt"> & { slug?: string },
  ) =>
    request<{ persona: Persona }>(`/api/personas/${encodeURIComponent(slug)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deletePersona: (slug: string) =>
    request<{ ok: boolean; slug: string; name: string }>(`/api/personas/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }),
};
