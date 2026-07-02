import type { FlowStepSummary, StepExecution } from "../api";
import { stepStatusLabel } from "../components/StepTimeline";

export function groupStepsIntoIterations(
  steps: StepExecution[],
  loopStartStepKey?: string | null,
): StepExecution[][] {
  if (steps.length === 0) {
    return [];
  }

  const splitKey = loopStartStepKey?.trim();
  if (!splitKey) {
    return [steps];
  }

  const iterations: StepExecution[][] = [];
  let current: StepExecution[] = [];

  for (const step of steps) {
    if (step.step_key === splitKey && current.length > 0) {
      iterations.push(current);
      current = [step];
    } else {
      current.push(step);
    }
  }

  if (current.length > 0) {
    iterations.push(current);
  }

  return iterations;
}

export function iterationHeadline(
  steps: StepExecution[],
  currentStep?: string | null,
  flowSteps?: FlowStepSummary[],
): string {
  if (steps.length === 0) {
    return "Not started";
  }

  const active =
    (currentStep ? steps.find((step) => step.step_key === currentStep) : undefined) ??
    steps.find((step) => step.status !== "completed") ??
    steps[steps.length - 1];

  const label =
    flowSteps?.find((step) => step.step_key === active.step_key)?.label ??
    active.step_key.replace(/_/g, " ");
  return `${label} · ${stepStatusLabel(active.status)}`;
}

export function iterationAccepted(steps: StepExecution[], flowSteps?: FlowStepSummary[]): boolean {
  const orderedFlow = flowSteps ? [...flowSteps].sort((left, right) => left.order - right.order) : [];
  const terminalKey = orderedFlow[orderedFlow.length - 1]?.step_key;
  if (terminalKey) {
    const terminal = [...steps].reverse().find((step) => step.step_key === terminalKey);
    return terminal?.status === "completed" && !terminal.error;
  }

  return steps.length > 0 && steps.every((step) => step.status === "completed" && !step.error);
}
