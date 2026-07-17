import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DurationStats, type FactoryStats } from "../api";
import { formatDuration } from "../utils/stepStats";
import { stepRoleLabel } from "../utils/stepRoleLabels";

function StatsSummary({ summary }: { summary: DurationStats }) {
  return (
    <dl className="stats-dl stats-summary-grid">
      <div>
        <dt>Completed steps</dt>
        <dd>{summary.count}</dd>
      </div>
      <div>
        <dt>Total LLM time</dt>
        <dd>{formatDuration(summary.total_duration_ms)}</dd>
      </div>
      <div>
        <dt>Average step time</dt>
        <dd>{formatDuration(summary.avg_duration_ms)}</dd>
      </div>
      <div>
        <dt>Median step time</dt>
        <dd>{formatDuration(summary.median_duration_ms)}</dd>
      </div>
    </dl>
  );
}

function StatsTable({
  title,
  columns,
  rows,
}: {
  title: string;
  columns: Array<{ key: string; label: string }>;
  rows: Array<Record<string, string | number>>;
}) {
  if (rows.length === 0) {
    return (
      <section className="stats-section">
        <h3>{title}</h3>
        <p className="hint">No completed steps recorded yet.</p>
      </section>
    );
  }

  return (
    <section className="stats-section">
      <h3>{title}</h3>
      <div className="stats-table-wrap">
        <table className="stats-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.label}</th>
              ))}
              <th>Steps</th>
              <th>Total time</th>
              <th>Average</th>
              <th>Median</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${title}-${index}`}>
                {columns.map((column) => (
                  <td key={column.key}>
                    {column.key === "step_key"
                      ? stepRoleLabel(String(row[column.key] ?? ""))
                      : String(row[column.key] ?? "—")}
                  </td>
                ))}
                <td>{row.count}</td>
                <td>{formatDuration(row.total_duration_ms as number)}</td>
                <td>{formatDuration(row.avg_duration_ms as number)}</td>
                <td>{formatDuration(row.median_duration_ms as number)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function StatsPage() {
  const [stats, setStats] = useState<FactoryStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .getStats(100)
      .then(setStats)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!stats) return <p>Loading stats…</p>;

  return (
    <section className="card stats-page">
      <h2>Factory stats</h2>
      <p className="hint">
        Step timing from completed control-plane executions, grouped by puller and model. Each row in
        recent steps links back to the prompt that produced it.
      </p>

      <StatsSummary summary={stats.summary} />

      <StatsTable
        title="By puller"
        columns={[{ key: "puller", label: "Puller" }]}
        rows={stats.by_puller}
      />
      <StatsTable
        title="By model"
        columns={[{ key: "model", label: "Model" }]}
        rows={stats.by_model}
      />
      <StatsTable
        title="By step"
        columns={[{ key: "step_key", label: "Step" }]}
        rows={stats.by_step}
      />
      <StatsTable
        title="Puller × step"
        columns={[
          { key: "puller", label: "Puller" },
          { key: "step_key", label: "Step" },
        ]}
        rows={stats.by_puller_step}
      />
      <StatsTable
        title="Model × step"
        columns={[
          { key: "model", label: "Model" },
          { key: "step_key", label: "Step" },
        ]}
        rows={stats.by_model_step}
      />

      <section className="stats-section">
        <h3>Recent steps</h3>
        {stats.recent_steps.length === 0 ? (
          <p className="hint">No completed steps recorded yet.</p>
        ) : (
          <div className="stats-table-wrap">
            <table className="stats-table stats-table-recent">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Prompt</th>
                  <th>Step</th>
                  <th>Puller</th>
                  <th>Model</th>
                  <th>Time</th>
                  <th>Turns</th>
                  <th>Run</th>
                </tr>
              </thead>
              <tbody>
                {stats.recent_steps.map((row) => (
                  <tr key={row.step_execution_id}>
                    <td>{row.completed_at ? new Date(row.completed_at).toLocaleString() : "—"}</td>
                    <td className="stats-prompt-cell">{row.prompt}</td>
                    <td>{stepRoleLabel(row.step_key)}</td>
                    <td>{row.puller}</td>
                    <td>{row.model}</td>
                    <td>{formatDuration(row.duration_ms)}</td>
                    <td>{row.turns ?? "—"}</td>
                    <td>
                      <Link to={`/runs/${row.run_id}`} className="run-link">
                        {row.run_id}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}
