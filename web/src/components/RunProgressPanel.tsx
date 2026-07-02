import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import StatsDisclosure, { aggregateStepStats } from "./StatsDisclosure";
import StepTimeline from "./StepTimeline";
import type { FlowStepSummary, StepExecution } from "../api";
import { groupStepsIntoIterations, iterationAccepted, iterationHeadline } from "../utils/groupRunSteps";
import { mergeFlowStepsWithExecutions } from "../utils/mergeRunSteps";

type Props = {
  title: string;
  status: string;
  statusLabel?: string;
  runId?: string;
  steps?: StepExecution[];
  flowSteps?: FlowStepSummary[];
  currentStep?: string | null;
  defaultOpen?: boolean;
  meta?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
};

function statusBadgeClass(status: string): string {
  if (status === "running") return "status-running";
  if (status === "completed") return "status-completed";
  if (status === "failed" || status === "cancelled") return "status-failed";
  if (status === "queued") return "status-queued";
  return "status-queued";
}

export default function RunProgressPanel({
  title,
  status,
  statusLabel,
  runId,
  steps = [],
  flowSteps = [],
  currentStep,
  defaultOpen = false,
  meta,
  actions,
  footer,
}: Props) {
  const orderedFlowSteps = [...flowSteps].sort((left, right) => left.order - right.order);
  const loopStartStepKey = orderedFlowSteps[0]?.step_key ?? null;
  const displaySteps = mergeFlowStepsWithExecutions(orderedFlowSteps, steps, currentStep);
  const iterations = groupStepsIntoIterations(steps, loopStartStepKey);
  const latestIndex = iterations.length - 1;
  const showIterationGroups = iterations.length > 1;
  const runActions = (
    <>
      {runId ? (
        <Link
          to={`/runs/${runId}`}
          className="secondary run-view-link"
          onClick={(event) => event.stopPropagation()}
        >
          View run
        </Link>
      ) : null}
      {actions}
    </>
  );

  return (
    <details className="disclosure-box run-article-box" open={defaultOpen || undefined}>
      <summary className="disclosure-box-summary run-article-summary">
        <span className="disclosure-chevron" aria-hidden="true" />
        <span className={`queue-status-badge ${statusBadgeClass(status)}`}>
          {statusLabel ?? status}
        </span>
        <span className="run-article-title">{title}</span>
        {meta ? (
          <span
            className="run-article-meta"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {meta}
          </span>
        ) : null}
        {runId || actions ? (
          <span
            className="run-article-actions"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {runActions}
          </span>
        ) : null}
      </summary>
      <div className="disclosure-box-body run-article-body">
        {runId || actions ? <div className="run-article-toolbar">{runActions}</div> : null}
        {footer}
        <StatsDisclosure stats={aggregateStepStats(steps)} label="Artifact statistics (so far)" />
        {!showIterationGroups && (
          <>
            {displaySteps.length === 0 && (
              <p className="hint">
                {currentStep
                  ? `Current stage: ${currentStep.replace(/_/g, " ")}`
                  : "No flow steps recorded yet."}
              </p>
            )}
            {displaySteps.length > 0 && <StepTimeline steps={displaySteps} />}
          </>
        )}
        {showIterationGroups &&
          iterations.map((iterationSteps, index) => {
            const isLatest = index === latestIndex;
            const iterationDisplaySteps = isLatest
              ? mergeFlowStepsWithExecutions(orderedFlowSteps, iterationSteps, currentStep)
              : iterationSteps;
            const accepted = iterationAccepted(iterationSteps, orderedFlowSteps);
            return (
              <details
                key={`iteration-${index}`}
                className="disclosure-box run-iteration-box"
                open={isLatest || undefined}
              >
                <summary className="disclosure-box-summary run-iteration-summary">
                  <span className="disclosure-chevron" aria-hidden="true" />
                  <strong>Iteration {index + 1}</strong>
                  <span className="run-iteration-meta">
                    {accepted
                      ? "Accepted"
                      : iterationHeadline(
                          iterationSteps,
                          isLatest ? currentStep : null,
                          orderedFlowSteps,
                        )}
                  </span>
                </summary>
                <div className="disclosure-box-body">
                  <StatsDisclosure stats={aggregateStepStats(iterationSteps)} label="Iteration statistics" />
                  <StepTimeline steps={iterationDisplaySteps} />
                </div>
              </details>
            );
          })}
      </div>
    </details>
  );
}
