import type { FlowStepSummary, StepExecution } from "../api";

export type StepExecutionView = StepExecution & {
  label?: string;
  isPlaceholder?: boolean;
};

function latestIterationStart(executions: StepExecution[], loopStartStepKey?: string | null): number {
  const splitKey = loopStartStepKey?.trim();
  if (!splitKey || executions.length === 0) {
    return 0;
  }
  for (let index = executions.length - 1; index > 0; index -= 1) {
    if (executions[index].step_key === splitKey) {
      return index;
    }
  }
  return 0;
}

export function mergeFlowStepsWithExecutions(
  flowSteps: FlowStepSummary[],
  executions: StepExecution[],
  currentStep?: string | null,
): StepExecutionView[] {
  if (flowSteps.length === 0) {
    return executions;
  }

  const orderedFlow = [...flowSteps].sort((left, right) => left.order - right.order);
  const loopStartStepKey = orderedFlow[0]?.step_key;
  const iterationExecutions = executions.slice(latestIterationStart(executions, loopStartStepKey));
  const usedExecutionIds = new Set<number>();
  const merged: StepExecutionView[] = [];

  for (const flowStep of orderedFlow) {
    const match = iterationExecutions.find(
      (execution) => execution.step_key === flowStep.step_key && !usedExecutionIds.has(execution.id),
    );
    if (match) {
      usedExecutionIds.add(match.id);
      merged.push({ ...match, label: flowStep.label });
      continue;
    }

    const isCurrent = flowStep.step_key === currentStep;
    merged.push({
      id: -flowStep.order,
      run_id: "",
      step_key: flowStep.step_key,
      label: flowStep.label,
      status: isCurrent ? "pending" : "pending",
      agent_id: "",
      conversation_id: "",
      puller: "",
      model: "",
      isPlaceholder: true,
    });
  }

  if (merged.length === 0) {
    return executions;
  }

  return merged;
}
