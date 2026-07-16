import type { ErrorGroupCount } from "../api";

const OUTCOME_COLORS: Record<string, string> = {
  completed: "#3fb950",
  running: "#d29922",
  queued: "#6e7681",
  cancelled: "#6e7681",
  iteration_limit: "#f85149",
  missing_verdict: "#ff7b72",
  puller_timeout: "#ffa657",
  llm_error: "#a371f7",
  run_interrupted: "#8b949e",
  failed_other: "#f85149",
};

type OutcomeCategoryChartProps = {
  rows: ErrorGroupCount[];
  totalRuns: number;
};

export default function OutcomeCategoryChart({ rows, totalRuns }: OutcomeCategoryChartProps) {
  if (!rows.length || totalRuns <= 0) {
    return null;
  }

  const maxCount = Math.max(...rows.map((row) => row.count), 1);

  return (
    <section className="flow-outcome-chart">
      <h3>Run outcomes</h3>
      <p className="hint">Where each dispatched run ended — {totalRuns} total in this cohort.</p>
      <div className="flow-outcome-chart-bars">
        {rows.map((row) => {
          const widthPct = Math.max(4, (row.count / maxCount) * 100);
          const sharePct = Math.round((row.count / totalRuns) * 100);
          return (
            <div className="flow-outcome-chart-row" key={row.error_group}>
              <span className="flow-outcome-chart-label">{row.error_group_label}</span>
              <div className="flow-outcome-chart-track" aria-hidden="true">
                <div
                  className="flow-outcome-chart-fill"
                  style={{
                    width: `${widthPct}%`,
                    backgroundColor: OUTCOME_COLORS[row.error_group] ?? "#388bfd",
                  }}
                />
              </div>
              <span className="flow-outcome-chart-count">
                {row.count} ({sharePct}%)
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
