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

type TurnOutcomeBarChartProps = {
  title: string;
  hint: string;
  rows: TurnCountRow[];
  total: number;
  barColor: string;
  emptyMessage?: string;
};

export default function TurnOutcomeBarChart({
  title,
  hint,
  rows,
  total,
  barColor,
  emptyMessage = "No data for this cohort yet.",
}: TurnOutcomeBarChartProps) {
  const activeRows = rows.filter((row) => row.count > 0);
  if (activeRows.length === 0) {
    return (
      <section className="flow-turn-chart">
        <h3>{title}</h3>
        <p className="hint">{hint}</p>
        <p className="hint">{emptyMessage}</p>
      </section>
    );
  }

  const maxCount = Math.max(...activeRows.map((row) => row.count), 1);

  return (
    <section className="flow-turn-chart">
      <h3>{title}</h3>
      <p className="hint">{hint}</p>
      <div className="flow-turn-chart-columns" role="img" aria-label={title}>
        {activeRows.map((row) => {
          const heightPct = Math.max(8, (row.count / maxCount) * 100);
          const sharePct = total > 0 ? Math.round((row.count / total) * 100) : 0;
          return (
            <div className="flow-turn-chart-column" key={row.turn}>
              <span className="flow-turn-chart-value">{row.count}</span>
              <div className="flow-turn-chart-bar-track">
                <div
                  className="flow-turn-chart-bar-fill"
                  style={{ height: `${heightPct}%`, backgroundColor: barColor }}
                />
              </div>
              <span className="flow-turn-chart-label">Cycle {row.turn}</span>
              <span className="flow-turn-chart-share">{sharePct}%</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

type TurnOutcomeChartPairProps = {
  charts: TurnOutcomeCharts;
};

export function TurnOutcomeChartPair({ charts }: TurnOutcomeChartPairProps) {
  const hasAny =
    charts.success_by_turn.some((row) => row.count > 0) ||
    charts.failure_by_turn.some((row) => row.count > 0);
  if (!hasAny) {
    return null;
  }

  return (
    <div className="flow-turn-chart-grid">
      <TurnOutcomeBarChart
        title="Artifacts by review cycle"
        hint={`${charts.success_total} completed run(s) — which review cycle produced the artifact.`}
        rows={charts.success_by_turn}
        total={charts.success_total}
        barColor="#3fb950"
        emptyMessage="No completed artifacts in this cohort yet."
      />
      <TurnOutcomeBarChart
        title="Failures by review cycle"
        hint={`${charts.failure_total} failed or cancelled run(s) — cycle where the run stopped.`}
        rows={charts.failure_by_turn}
        total={charts.failure_total}
        barColor="#f85149"
        emptyMessage="No failures in this cohort."
      />
    </div>
  );
}
