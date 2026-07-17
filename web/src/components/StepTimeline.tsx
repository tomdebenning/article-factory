import { Link } from "react-router-dom";
import StatsDisclosure from "./StatsDisclosure";
import ToolUseDisclosure from "./ToolUseDisclosure";
import { stepRoleLabel } from "../utils/stepRoleLabels";
import { isStepInFlight, stepActivityLabel, stepCpRound } from "../utils/stepProgress";
import { statsForStep, stepTurns } from "../utils/stepStats";

type StepView = StepExecution & { label?: string; isPlaceholder?: boolean };

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  submitted: "Sent to control plane",
  waiting: "Waiting for puller",
  pulled: "Puller fetched task",
  completed: "Completed",
  failed: "Failed",
};

export function stepStatusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function canShowResponse(step: StepExecution): boolean {
  return Boolean(
    (step.status === "completed" && step.response_content) ||
      (step.status === "failed" && step.error),
  );
}

export default function StepTimeline({ steps }: { steps: StepView[] }) {
  if (steps.length === 0) {
    return <p className="hint">No control-plane steps recorded yet.</p>;
  }

  return (
    <ol className="step-timeline">
      {steps.map((step) => {
        const inFlight = isStepInFlight(step.status);
        const activity = stepActivityLabel(step);
        const cpRound = stepCpRound(step);
        const toolCount = step.tools_used?.length ?? 0;

        return (
        <li
          key={`${step.id}:${step.step_key}`}
          className={`step-timeline-item status-${step.status}${step.isPlaceholder ? " is-placeholder" : ""}${inFlight ? " is-live" : ""}`}
        >
          <div className="step-timeline-head">
            <strong>{stepRoleLabel(step.step_key, step.label)}</strong>
            <span>{stepStatusLabel(step.status)}</span>
          </div>
          {!step.isPlaceholder && (
            <>
              {inFlight && activity && (
                <p className="step-live-activity">
                  <span className="step-live-dot" aria-hidden="true" />
                  {activity}
                </p>
              )}
              <div className="step-timeline-meta">
                {step.puller && <span>puller: {step.puller}</span>}
                {step.model && <span>model: {step.model}</span>}
                {step.cp_queue_depth != null && <span>CP queue: {step.cp_queue_depth}</span>}
                {cpRound != null && cpRound > 1 ? <span>CP round: {cpRound}</span> : null}
                {(step.status === "completed" || toolCount > 0) && stepTurns(step) > 0 ? (
                  <span>turns: {stepTurns(step)}</span>
                ) : null}
                {toolCount > 0 ? <span>tools: {toolCount}</span> : null}
              </div>
              {step.conversation_id && (
                <div className="step-timeline-meta">
                  <span>agent: {step.agent_id}</span>
                  <span>conv: {step.conversation_id}</span>
                </div>
              )}
              {step.error && !step.response_content && <p className="error">{step.error}</p>}
              <StatsDisclosure stats={statsForStep(step)} label="Step statistics" />
              <ToolUseDisclosure tools={step.tools_used || []} live={inFlight} />
              {canShowResponse(step) && (
                <details className="step-response-details">
                  <summary>{step.status === "failed" ? "View error" : "View response"}</summary>
                  {step.response_content ? (
                    <pre className="step-response-body">{step.response_content}</pre>
                  ) : (
                    step.error && <pre className="step-response-body step-response-error">{step.error}</pre>
                  )}
                </details>
              )}
            </>
          )}
        </li>
        );
      })}
    </ol>
  );
}

export function RunLink({ runId }: { runId: string }) {
  return (
    <Link to={`/runs/${runId}`} className="run-link">
      {runId}
    </Link>
  );
}
