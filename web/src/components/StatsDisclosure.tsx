import { aggregateStepStats, formatDuration, hasAnyStats, type AggregatedStats } from "../utils/stepStats";
import { formatTokenCount } from "../utils/tokenFormat";

type Props = {
  stats: AggregatedStats;
  label?: string;
};

export default function StatsDisclosure({ stats, label = "Statistics" }: Props) {
  if (!hasAnyStats(stats)) {
    return (
      <details className="stats-disclosure">
        <summary>{label}</summary>
        <p className="hint stats-disclosure-empty">No statistics recorded yet.</p>
      </details>
    );
  }

  return (
    <details className="stats-disclosure">
      <summary>{label}</summary>
      <dl className="stats-dl stats-disclosure-body">
        <div>
          <dt>Turns</dt>
          <dd>{stats.turns || stats.llm_calls || 0}</dd>
        </div>
        <div>
          <dt>Input tokens</dt>
          <dd>{formatTokenCount(stats.input_tokens)}</dd>
        </div>
        <div>
          <dt>Output tokens</dt>
          <dd>{formatTokenCount(stats.output_tokens)}</dd>
        </div>
        <div>
          <dt>Total tokens</dt>
          <dd>{formatTokenCount(stats.total_tokens)}</dd>
        </div>
        <div>
          <dt>Total time</dt>
          <dd>{formatDuration(stats.total_duration_ms)}</dd>
        </div>
      </dl>
    </details>
  );
}

export { aggregateStepStats };
