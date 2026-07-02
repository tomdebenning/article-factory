import type { StepExecution } from "../api";

const IN_FLIGHT_STATUSES = new Set(["pending", "submitted", "waiting", "pulled"]);

export function isStepInFlight(status: string): boolean {
  return IN_FLIGHT_STATUSES.has(status);
}

export function stepActivityLabel(step: StepExecution): string | null {
  const activity = step.progress?.activity?.trim();
  if (activity) {
    return activity;
  }
  if (step.status === "waiting") {
    return "Waiting for puller";
  }
  if (step.status === "pulled") {
    return "Puller generating response";
  }
  if (step.status === "submitted") {
    return "Submitted to control plane";
  }
  return null;
}

export function stepCpRound(step: StepExecution): number | null {
  const fromProgress = step.progress?.cp_round;
  if (typeof fromProgress === "number" && fromProgress > 0) {
    return fromProgress;
  }
  if (typeof step.turns === "number" && step.turns > 0) {
    return step.turns;
  }
  return null;
}
