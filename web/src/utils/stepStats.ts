import type { StepExecution, StepUsage } from "../api";

export type AggregatedStats = {
  llm_calls: number;
  turns: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_duration_ms: number;
};

export function formatDuration(ms?: number | null): string {
  if (!ms || ms <= 0) return "—";

  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  const secondsRemainder = (ms % 60_000) / 1000;
  const showFraction = ms < 60_000 && ms % 1000 !== 0;
  const seconds = showFraction
    ? secondsRemainder.toFixed(1).padStart(4, "0")
    : String(Math.floor(secondsRemainder)).padStart(2, "0");

  return `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`;
}

export function stepDurationMs(step: StepExecution): number {
  if (step.duration_ms && step.duration_ms > 0) {
    return step.duration_ms;
  }
  const started = step.started_at ? Date.parse(step.started_at) : NaN;
  const ended = step.completed_at ? Date.parse(step.completed_at) : Date.now();
  if (!Number.isFinite(started)) {
    return 0;
  }
  return Math.max(0, ended - started);
}

export function stepTurns(step: StepExecution): number {
  if (step.turns != null && step.turns > 0) {
    return step.turns;
  }
  const tools = step.tools_used || [];
  if (tools.length > 0) {
    return Math.max(...tools.map((entry) => entry.round ?? 1));
  }
  if (step.status === "completed") {
    return 1;
  }
  return 0;
}

export function normalizeUsage(usage?: StepUsage | null): StepUsage {
  return {
    input_tokens: Number(usage?.input_tokens) || 0,
    output_tokens: Number(usage?.output_tokens) || 0,
    total_tokens: Number(usage?.total_tokens) || 0,
  };
}

export function aggregateStepStats(steps: StepExecution[]): AggregatedStats {
  const totals: AggregatedStats = {
    llm_calls: 0,
    turns: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    total_duration_ms: 0,
  };

  for (const step of steps) {
    const turns = stepTurns(step);
    totals.turns += turns;
    totals.llm_calls += turns;
    const usage = normalizeUsage(step.usage);
    totals.input_tokens += usage.input_tokens ?? 0;
    totals.output_tokens += usage.output_tokens ?? 0;
    totals.total_tokens += usage.total_tokens ?? 0;
    totals.total_duration_ms += stepDurationMs(step);
  }

  return totals;
}

export function hasAnyStats(stats: AggregatedStats): boolean {
  return stats.turns > 0 || stats.total_tokens > 0 || stats.total_duration_ms > 0;
}

export function statsForStep(step: StepExecution): AggregatedStats {
  const usage = normalizeUsage(step.usage);
  const turns = stepTurns(step);
  return {
    llm_calls: turns,
    turns,
    input_tokens: usage.input_tokens ?? 0,
    output_tokens: usage.output_tokens ?? 0,
    total_tokens: usage.total_tokens ?? 0,
    total_duration_ms: stepDurationMs(step),
  };
}
