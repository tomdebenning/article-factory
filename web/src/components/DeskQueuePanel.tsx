import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type QueueItem, type RunSummary } from "../api";

type Props = {
  deskPath: string;
  refreshToken?: number;
};

function flowMatchesDesk(itemFlowPath: string | undefined, deskPath: string): boolean {
  const normalized = (itemFlowPath || "").trim();
  if (!normalized) {
    return false;
  }
  return normalized === deskPath.trim();
}

function stepLabel(stepKey?: string | null): string {
  if (!stepKey) {
    return "Starting";
  }
  return stepKey.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export default function DeskQueuePanel({ deskPath, refreshToken = 0 }: Props) {
  const [queued, setQueued] = useState<QueueItem[]>([]);
  const [running, setRunning] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    void Promise.all([api.listQueue(), api.factoryStatus()])
      .then(([queueResult, status]) => {
        setQueued(
          queueResult.items.filter(
            (item) =>
              flowMatchesDesk(item.flow_path, deskPath) &&
              (item.status === "queued" || item.status === "running"),
          ),
        );
        setRunning(
          (status.active_runs || []).filter((run) => (run.flow_path || "").trim() === deskPath.trim()),
        );
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [deskPath]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load, refreshToken]);

  const totalAtPlay = useMemo(() => queued.length + running.length, [queued.length, running.length]);

  return (
    <div className="desk-queue-panel">
      <div className="desk-queue-summary">
        <p className="hint">
          Topics waiting or actively writing on this desk. The factory runs one article at a time; extras stay queued
          until a puller is free.
        </p>
        <dl className="desk-queue-stats">
          <div>
            <dt>At play</dt>
            <dd>{totalAtPlay}</dd>
          </div>
          <div>
            <dt>Running</dt>
            <dd>{running.length}</dd>
          </div>
          <div>
            <dt>Queued</dt>
            <dd>{queued.length}</dd>
          </div>
        </dl>
      </div>

      {error && <p className="error">{error}</p>}

      <section className="desk-queue-section">
        <h4 className="desk-subsection-title">Running now</h4>
        {running.length === 0 ? (
          <p className="hint desk-queue-empty">No articles in progress on this desk.</p>
        ) : (
          <ul className="desk-queue-run-list">
            {running.map((run) => (
              <li key={run.run_id} className="desk-queue-run-item is-running">
                <div className="desk-queue-run-head">
                  <strong>{stepLabel(run.current_step)}</strong>
                  <span className="desk-queue-run-meta">
                    Draft {run.draft_number ?? 1}
                    {run.review_round ? ` · review ${run.review_round}` : ""}
                  </span>
                </div>
                <p className="desk-queue-run-prompt">
                  {run.topic_prompt?.trim() || run.topic_slug || run.run_id}
                </p>
                <Link to={`/active?run=${encodeURIComponent(run.run_id)}`} className="secondary">
                  Open on Active board
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="desk-queue-section">
        <h4 className="desk-subsection-title">Queued next</h4>
        {queued.length === 0 ? (
          <p className="hint desk-queue-empty">Nothing queued — generate topics above and queue them to run.</p>
        ) : (
          <ul className="desk-queue-run-list">
            {queued.map((item) => (
              <li key={item.id} className={`desk-queue-run-item is-${item.status}`}>
                <div className="desk-queue-run-head">
                  <strong>{item.status === "running" ? "Starting" : "Waiting"}</strong>
                  <span className="desk-queue-run-meta">Queue #{item.id}</span>
                </div>
                <p className="desk-queue-run-prompt">{item.prompt.trim()}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export function countDeskQueueItems(
  deskPath: string,
  queued: QueueItem[],
  running: RunSummary[],
): number {
  const path = deskPath.trim();
  const queuedCount = queued.filter(
    (item) =>
      flowMatchesDesk(item.flow_path, path) &&
      (item.status === "queued" || item.status === "running"),
  ).length;
  const runningCount = running.filter((run) => (run.flow_path || "").trim() === path).length;
  return queuedCount + runningCount;
}
