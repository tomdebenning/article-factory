import type { RefObject, ReactNode } from "react";
import type { PromptImprovementReport } from "../api";

type Props = {
  report: PromptImprovementReport;
  panelRef?: RefObject<HTMLElement | null>;
};

function renderInlineMarkdown(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

function renderDetailedReport(text: string): ReactNode {
  const lines = text.split("\n");
  const nodes: ReactNode[] = [];
  let listItems: string[] = [];

  const flushList = () => {
    if (listItems.length === 0) return;
    nodes.push(
      <ul key={`list-${nodes.length}`}>
        {listItems.map((item, index) => (
          <li key={index}>{renderInlineMarkdown(item)}</li>
        ))}
      </ul>,
    );
    listItems = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }
    if (trimmed.startsWith("## ")) {
      flushList();
      nodes.push(<h5 key={`h2-${nodes.length}`}>{trimmed.slice(3)}</h5>);
      continue;
    }
    if (trimmed.startsWith("### ")) {
      flushList();
      nodes.push(<h6 key={`h3-${nodes.length}`}>{trimmed.slice(4)}</h6>);
      continue;
    }
    if (trimmed.startsWith("- ")) {
      listItems.push(trimmed.slice(2));
      continue;
    }
    flushList();
    nodes.push(<p key={`p-${nodes.length}`}>{renderInlineMarkdown(trimmed)}</p>);
  }
  flushList();
  return nodes;
}

function formatRunBucket(bucket: string | undefined): string {
  if (bucket === "top") return "top 25%";
  if (bucket === "bottom") return "bottom 25%";
  return bucket || "example";
}

export default function ImprovementReportPanel({ report, panelRef }: Props) {
  const successExamples = Array.isArray(report.example_runs?.success)
    ? (report.example_runs.success as Array<Record<string, unknown>>)
    : [];
  const failureExamples = Array.isArray(report.example_runs?.failure)
    ? (report.example_runs.failure as Array<Record<string, unknown>>)
    : [];

  const hasAnalysis =
    Boolean(report.detailed_report?.trim()) ||
    (report.actionable_items || []).length > 0 ||
    successExamples.length > 0 ||
    failureExamples.length > 0;

  return (
    <section
      ref={panelRef}
      id="flow-improvement-report"
      className="flow-improvement-report-panel"
    >
      <h4>Why prompts were changed</h4>
      <p className="hint">
        Analysis, conclusions, and evidence from the improvement run that created this version.
      </p>

      {report.summary && (
        <div className="flow-improvement-report-block">
          <strong>Version changelog</strong>
          <p>{report.summary}</p>
        </div>
      )}

      {report.detailed_report?.trim() ? (
        <div className="flow-improvement-report-block flow-improvement-report-analysis">
          <strong>Analysis &amp; conclusions</strong>
          <div className="flow-improvement-report-markdown">
            {renderDetailedReport(report.detailed_report)}
          </div>
        </div>
      ) : null}

      {(report.prompt_changes || []).length > 0 && (
        <div className="flow-improvement-report-block">
          <strong>Prompt change rationale</strong>
          <p className="hint">What was changed in each step and why, based on the analysis above.</p>
          <div className="flow-improvement-change-cards">
            {report.prompt_changes.map((change, index) => (
              <article key={`${change.step_key}-${index}`} className="flow-improvement-change-card">
                <header>
                  <strong>{change.label || change.step_key}</strong>
                  {change.fields?.length ? (
                    <span className="hint"> — {change.fields.join(", ")}</span>
                  ) : null}
                </header>
                {change.conclusion ? (
                  <p>
                    <strong>Conclusion:</strong> {change.conclusion}
                  </p>
                ) : null}
                {change.rationale ? (
                  <p>
                    <strong>Why:</strong> {change.rationale}
                  </p>
                ) : (
                  <p className="hint">No written rationale was recorded for this step.</p>
                )}
                {change.evidence_run_ids?.length ? (
                  <p className="hint">
                    <strong>Evidence runs:</strong> {change.evidence_run_ids.join(", ")}
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        </div>
      )}

      {(report.actionable_items || []).length > 0 && (
        <div className="flow-improvement-report-block">
          <strong>Follow-up recommendations</strong>
          <ul className="flow-improvement-action-list">
            {report.actionable_items.map((item, index) => (
              <li key={index}>
                <strong>{item.title}</strong>
                {item.priority ? ` (${item.priority})` : ""}
                {item.rationale ? (
                  <>
                    <br />
                    <span className="hint">{item.rationale}</span>
                  </>
                ) : null}
                {item.evidence_run_ids?.length ? (
                  <>
                    <br />
                    <span className="hint">Evidence: {item.evidence_run_ids.join(", ")}</span>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(successExamples.length > 0 || failureExamples.length > 0) && (
        <details className="flow-improvement-report-block flow-improvement-evidence">
          <summary>Telemetry examples reviewed ({successExamples.length + failureExamples.length} runs)</summary>
          <p className="hint">
            Top and bottom quartile runs used as evidence when the model analyzed performance.
          </p>
          {successExamples.length > 0 && (
            <div>
              <strong>Strong runs (top 25%)</strong>
              <ul>
                {successExamples.map((run, index) => (
                  <li key={`success-${index}`}>
                    {String(run.run_id || "run")} — score {String(run.composite_score ?? "—")}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {failureExamples.length > 0 && (
            <div>
              <strong>Weak runs (bottom 25%)</strong>
              <ul>
                {failureExamples.map((run, index) => (
                  <li key={`failure-${index}`}>
                    {String(run.run_id || "run")} — score {String(run.composite_score ?? "—")} (
                    {formatRunBucket(run.bucket as string | undefined)})
                  </li>
                ))}
              </ul>
            </div>
          )}
        </details>
      )}

      {!hasAnalysis &&
        !(report.prompt_changes || []).length &&
        !report.summary && (
          <p className="hint">This report has no written analysis yet.</p>
        )}
    </section>
  );
}
