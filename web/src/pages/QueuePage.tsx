import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import RunProgressPanel from "../components/RunProgressPanel";
import { api, QUEUE_STATUS_LABEL, type ActiveOverview, type RunSummary } from "../api";
import {
  formatRunTime,
  groupHistoryRuns,
  type HistoryDay,
} from "../utils/groupHistoryRuns";

type TabId = "running" | "history";

function runTitle(run: RunSummary): string {
  return run.topic_prompt?.trim() || run.run_id;
}

function runStatusLabel(run: RunSummary): string {
  return QUEUE_STATUS_LABEL[run.status] ?? run.status;
}

function RunRow({
  run,
  runActionId,
  onStop,
  onDelete,
}: {
  run: RunSummary;
  runActionId: string | null;
  onStop?: (run: RunSummary) => void;
  onDelete: (run: RunSummary) => void;
}) {
  const isRunning = run.status === "running";

  return (
    <RunProgressPanel
      title={runTitle(run)}
      status={run.status}
      statusLabel={runStatusLabel(run)}
      runId={run.run_id}
      steps={run.steps}
      flowSteps={run.flow_steps}
      currentStep={run.current_step}
      defaultOpen={isRunning}
      meta={
        <>
          <span className="hint active-run-time">{formatRunTime(run.started_at)}</span>
          {run.flow_path && (
            <Link
              to={`/flows/edit?path=${encodeURIComponent(run.flow_path)}`}
              className="hint flow-path-link"
              onClick={(e) => e.stopPropagation()}
            >
              {run.flow_path}
            </Link>
          )}
          <Link to={`/runs/${run.run_id}`} className="run-link" onClick={(e) => e.stopPropagation()}>
            {run.run_id}
          </Link>
        </>
      }
      actions={
        <>
          {run.status === "completed" && (
            <Link to={`/articles/${run.run_id}`} className="secondary" onClick={(e) => e.stopPropagation()}>
              View artifact
            </Link>
          )}
          {isRunning && onStop && (
            <button
              type="button"
              className="secondary"
              disabled={runActionId === run.run_id}
              onClick={(e) => {
                e.stopPropagation();
                onStop(run);
              }}
            >
              {runActionId === run.run_id ? "Stopping…" : "Stop run"}
            </button>
          )}
          {!isRunning && (
            <button
              type="button"
              className="secondary run-delete-button"
              disabled={runActionId === run.run_id}
              onClick={(e) => {
                e.stopPropagation();
                onDelete(run);
              }}
            >
              {runActionId === run.run_id ? "Deleting…" : "Delete run"}
            </button>
          )}
        </>
      }
      footer={
        run.error ? (
          <p className="queue-failure-reason">
            <strong>Why it failed:</strong> {run.error}
          </p>
        ) : undefined
      }
    />
  );
}

function RunningTab({
  groups,
  runActionId,
  onStop,
  onDelete,
}: {
  groups: ActiveOverview["running_groups"];
  runActionId: string | null;
  onStop: (run: RunSummary) => void;
  onDelete: (run: RunSummary) => void;
}) {
  if (groups.length === 0) {
    return (
      <p className="hint">
        No queues are running right now. Start one from{" "}
        <Link to="/start-flows">Start flows</Link>.
      </p>
    );
  }

  return (
    <div className="active-running-list">
      {groups.map((group) => {
        const groupKey = `${group.queue_id ?? "none"}:${group.flow_path}:${group.model}`;
        return (
          <details key={groupKey} className="disclosure-box active-group-box" open={group.running_count > 0 || undefined}>
            <summary className="disclosure-box-summary active-group-summary">
              <span className="disclosure-chevron" aria-hidden="true" />
              <div className="active-group-heading">
                <strong>{group.queue_name}</strong>
                <span className="hint active-group-meta">
                  {group.model} · {group.flow_path || "No flow"}
                </span>
              </div>
              <span className="active-group-counts">
                {group.running_count > 0 && (
                  <span className="queue-status-badge status-running">
                    {group.running_count} running
                  </span>
                )}
                {group.queued_count > 0 && (
                  <span className="queue-status-badge status-queued">
                    {group.queued_count} queued
                  </span>
                )}
              </span>
            </summary>
            <div className="disclosure-box-body active-group-body">
              {group.runs.length === 0 ? (
                <p className="hint">Topics are queued — waiting for an idle puller.</p>
              ) : (
                group.runs.map((run) => (
                  <RunRow
                    key={run.run_id}
                    run={run}
                    runActionId={runActionId}
                    onStop={onStop}
                    onDelete={onDelete}
                  />
                ))
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function HistoryTab({
  days,
  runActionId,
  onDelete,
}: {
  days: HistoryDay[];
  runActionId: string | null;
  onDelete: (run: RunSummary) => void;
}) {
  if (days.length === 0) {
    return <p className="hint">No previous runs yet.</p>;
  }

  return (
    <div className="active-history-list">
      {days.map((day) => (
        <details key={day.dayKey} className="disclosure-box active-day-box">
          <summary className="disclosure-box-summary active-day-summary">
            <span className="disclosure-chevron" aria-hidden="true" />
            <strong>{day.label}</strong>
            <span className="hint active-day-count">{day.runCount} run(s)</span>
          </summary>
          <div className="disclosure-box-body active-day-body">
            {day.slots.map((slot) => (
              <details key={slot.slot} className="disclosure-box active-slot-box">
                <summary className="disclosure-box-summary active-slot-summary">
                  <span className="disclosure-chevron" aria-hidden="true" />
                  <strong>{slot.label}</strong>
                  <span className="hint active-slot-count">{slot.runCount} run(s)</span>
                </summary>
                <div className="disclosure-box-body active-slot-body">
                  {slot.groups.map((group) => {
                    const groupKey = `${group.queue_name}:${group.flow_path}:${group.model}`;
                    return (
                      <details key={groupKey} className="disclosure-box active-history-group">
                        <summary className="disclosure-box-summary active-history-group-summary">
                          <span className="disclosure-chevron" aria-hidden="true" />
                          <strong>{group.queue_name}</strong>
                          <span className="hint">
                            {group.model} · {group.flow_path || "No flow"} · {group.runs.length} run(s)
                          </span>
                        </summary>
                        <div className="disclosure-box-body">
                          {group.runs.map((run) => (
                            <RunRow
                              key={run.run_id}
                              run={run}
                              runActionId={runActionId}
                              onDelete={onDelete}
                            />
                          ))}
                        </div>
                      </details>
                    );
                  })}
                </div>
              </details>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}

export default function QueuePage() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab: TabId = tabParam === "history" ? "history" : "running";
  const [overview, setOverview] = useState<ActiveOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(
    (location.state as { message?: string } | null)?.message ?? null,
  );
  const [runActionId, setRunActionId] = useState<string | null>(null);

  const setTab = (next: TabId) => {
    if (next === "running") {
      setSearchParams({});
    } else {
      setSearchParams({ tab: next });
    }
  };

  const historyDays = useMemo(
    () => groupHistoryRuns(overview?.history_runs ?? []),
    [overview?.history_runs],
  );

  const reload = () => {
    void api
      .activeOverview()
      .then((data) => {
        setOverview(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    reload();
    const timer = setInterval(reload, 4000);
    return () => clearInterval(timer);
  }, []);

  const stopRun = (run: RunSummary) => {
    setRunActionId(run.run_id);
    setError(null);
    void api
      .stopRun(run.run_id)
      .then((result) => {
        setMessage(result.message);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRunActionId(null));
  };

  const deleteRun = (run: RunSummary) => {
    if (!window.confirm(`Delete run ${run.run_id}?`)) {
      return;
    }
    setRunActionId(run.run_id);
    setError(null);
    void api
      .deleteRun(run.run_id)
      .then(() => {
        setMessage("Run deleted.");
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRunActionId(null));
  };

  const runningCount = overview?.running_groups.reduce((sum, group) => sum + group.running_count, 0) ?? 0;
  const historyCount = overview?.history_runs.length ?? 0;

  const stopAllRuns = () => {
    if (!window.confirm(`Stop all ${runningCount} running article(s)?`)) {
      return;
    }
    setRunActionId("__all__");
    setError(null);
    void api
      .stopAllRuns()
      .then((result) => {
        setMessage(result.message);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRunActionId(null));
  };

  return (
    <section className="card active-page">
      <h2>Active</h2>
      <p className="hint">
        Running queues grouped by flow and model. Previous runs are organized by day and six-hour window.
        Start new work on <Link to="/start-flows">Start flows</Link>.
      </p>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      <div className="active-tabs-row">
        <div className="active-tabs" role="tablist" aria-label="Active views">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "running"}
            className={tab === "running" ? "active-tab is-selected" : "active-tab"}
            onClick={() => setTab("running")}
          >
            Running
            {runningCount > 0 && <span className="active-tab-badge">{runningCount}</span>}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "history"}
            className={tab === "history" ? "active-tab is-selected" : "active-tab"}
            onClick={() => setTab("history")}
          >
            Previous runs
            {historyCount > 0 && <span className="active-tab-badge">{historyCount}</span>}
          </button>
        </div>
        {tab === "running" && runningCount > 0 && (
          <button
            type="button"
            className="secondary active-stop-all"
            disabled={runActionId === "__all__"}
            onClick={stopAllRuns}
          >
            {runActionId === "__all__" ? "Stopping…" : "Stop all running"}
          </button>
        )}
      </div>

      <div role="tabpanel" className="active-tab-panel">
        {tab === "running" ? (
          <RunningTab
            groups={overview?.running_groups ?? []}
            runActionId={runActionId}
            onStop={stopRun}
            onDelete={deleteRun}
          />
        ) : (
          <HistoryTab days={historyDays} runActionId={runActionId} onDelete={deleteRun} />
        )}
      </div>
    </section>
  );
}
