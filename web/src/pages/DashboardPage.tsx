import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import MorningShiftWizard from "../components/MorningShiftWizard";
import RunProgressPanel from "../components/RunProgressPanel";
import {
  api,
  QUEUE_STATUS_LABEL,
  type CompletedArticle,
  type FactoryStatus,
  type ReadinessCheck,
  type RunSummary,
} from "../api";
import { deskCoverageMeta, deskCoverageSubtitle, deskCoverageTitle, deskDetailUrl, loadDeskSummaries, type DeskSummary } from "../utils/desks";

function ReadinessChecklist({ checks }: { checks: ReadinessCheck[] }) {
  return (
    <ul className="readiness-list">
      {checks.map((check) => (
        <li key={check.id} className={check.ok ? "readiness-ok" : "readiness-fail"}>
          <span className="readiness-icon">{check.ok ? "✓" : "○"}</span>
          <div className="readiness-body">
            <strong>{check.label}</strong>
            <p className="hint">{check.message}</p>
            {!check.ok && check.action_label && check.action_path && (
              <Link to={check.action_path} className="readiness-action">
                {check.action_label} →
              </Link>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function ActiveRunCard({
  run,
  busyRunId,
  deskLabel,
  onStop,
}: {
  run: RunSummary;
  busyRunId: string | null;
  deskLabel?: string;
  onStop: (run: RunSummary) => void;
}) {
  const isRunning = run.status === "running";

  return (
    <RunProgressPanel
      title={run.topic_prompt ?? run.run_id}
      status={run.status}
      statusLabel={QUEUE_STATUS_LABEL[run.status] ?? run.status}
      runId={run.run_id}
      steps={run.steps}
      flowSteps={run.flow_steps}
      currentStep={run.current_step}
      defaultOpen
      meta={
        <span className="hint">
          {run.flow_path && (
            <>
              <Link to={deskDetailUrl(run.flow_path)}>{deskLabel ?? run.flow_path}</Link>
              {" · "}
            </>
          )}
          {run.selected_model ? `Model: ${run.selected_model}` : null}
          {run.selected_model && run.selected_puller ? " · " : null}
          {run.selected_puller ? `Puller: ${run.selected_puller}` : null}
          {" · "}
          <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
        </span>
      }
      actions={
        isRunning ? (
          <button
            type="button"
            className="secondary"
            disabled={busyRunId === run.run_id}
            onClick={(event) => {
              event.stopPropagation();
              onStop(run);
            }}
          >
            {busyRunId === run.run_id ? "Stopping…" : "Stop run"}
          </button>
        ) : undefined
      }
    />
  );
}

function DeskDashboardTile({
  desk,
  runningCount,
}: {
  desk: DeskSummary;
  runningCount: number;
}) {
  return (
    <Link to={deskDetailUrl(desk.path)} className={`desk-tile desk-tile-dashboard${runningCount > 0 ? " is-active" : ""}`}>
      <span className="desk-tile-label">{deskCoverageTitle(desk)}</span>
      <span className="desk-tile-role">{deskCoverageSubtitle(desk)}</span>
      <span className="desk-tile-meta">
        {runningCount > 0 ? `${runningCount} assignment${runningCount === 1 ? "" : "s"} running · ` : ""}
        {deskCoverageMeta(desk)}
      </span>
    </Link>
  );
}

export default function DashboardPage() {
  const [status, setStatus] = useState<FactoryStatus | null>(null);
  const [desks, setDesks] = useState<DeskSummary[]>([]);
  const [articles, setArticles] = useState<CompletedArticle[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [stopRunId, setStopRunId] = useState<string | null>(null);

  const reload = () => {
    void Promise.all([
      api.factoryStatus(),
      loadDeskSummaries(api.getFlowTree, api.listFlows),
      api.listArticles(),
    ])
      .then(([nextStatus, deskList, articleList]) => {
        setStatus(nextStatus);
        setDesks(deskList);
        setArticles(articleList.articles.slice(0, 5));
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
    setStopRunId(run.run_id);
    setError(null);
    setMessage(null);
    void api
      .stopRun(run.run_id)
      .then((result) => {
        setMessage(result.message);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setStopRunId(null));
  };

  const runsByDesk = useMemo(() => {
    const map = new Map<string, number>();
    if (!status) {
      return map;
    }
    const runs =
      status.active_runs?.length > 0
        ? status.active_runs
        : status.active_run
          ? [status.active_run]
          : [];
    for (const run of runs) {
      if (!run.flow_path) {
        continue;
      }
      map.set(run.flow_path, (map.get(run.flow_path) || 0) + 1);
    }
    return map;
  }, [status]);

  const deskLabelByPath = useMemo(() => {
    const map = new Map<string, string>();
    for (const desk of desks) {
      map.set(desk.path, deskCoverageTitle(desk));
    }
    return map;
  }, [desks]);

  if (error) {
    return (
      <section className="card home-page">
        <h2>Cannot reach the factory</h2>
        <p className="error">{error}</p>
        <p className="hint">
          Make sure the factory is running and your API key in Settings matches the server.
        </p>
        <Link to="/settings" className="secondary">
          Open Settings
        </Link>
      </section>
    );
  }

  if (!status) {
    return (
      <section className="card home-page">
        <p className="hint">Loading dashboard…</p>
      </section>
    );
  }

  const readiness = status.readiness;
  const issueChecks = readiness.issue_checks ?? readiness.checks.filter((check) => !check.ok && check.id !== "topics");
  const activeRuns =
    status.active_runs?.length > 0
      ? status.active_runs
      : status.active_run
        ? [status.active_run]
        : [];
  const queuedCount = status.queue_counts?.queued ?? status.queue_depth ?? 0;
  const isWriting = activeRuns.length > 0;
  const missingCount = issueChecks.length;

  return (
    <div className="home-page newsroom-dashboard">
      <section className="card dashboard-status-strip">
        <div className="dashboard-status-metrics">
          <div className={`dashboard-metric${isWriting ? " is-live" : ""}`}>
            <span className="dashboard-metric-value">{activeRuns.length}</span>
            <span className="dashboard-metric-label">Running</span>
          </div>
          <div className={`dashboard-metric${queuedCount > 0 ? " is-warn" : ""}`}>
            <span className="dashboard-metric-value">{queuedCount}</span>
            <span className="dashboard-metric-label">Queued</span>
          </div>
          <div className={`dashboard-metric${missingCount > 0 ? " is-warn" : ""}`}>
            <span className="dashboard-metric-value">{missingCount}</span>
            <span className="dashboard-metric-label">Needs attention</span>
          </div>
          <div className="dashboard-metric">
            <span className="dashboard-metric-value">{desks.length}</span>
            <span className="dashboard-metric-label">Desks</span>
          </div>
        </div>
        <p className="hint dashboard-status-summary">
          {readiness.setup_complete
            ? readiness.summary
            : "Finish setup before the newsroom can run shifts reliably."}
        </p>
      </section>

      {status.onboarding && (
        <MorningShiftWizard
          onboarding={status.onboarding}
          setupComplete={readiness.setup_complete}
        />
      )}

      <section className="card dashboard-section">
        <div className="dashboard-section-head">
          <div>
            <h3>Your desks</h3>
            <p className="hint">
              Each desk is a beat — what to cover on an Edition topic. Pipeline prompts are configured inside the desk.
            </p>
          </div>
          {desks.length > 0 && (
            <Link to="/flows/new" className="secondary">
              Create desk
            </Link>
          )}
        </div>
        {desks.length === 0 ? (
          <div className="desk-empty-panel">
            <p>No desks configured yet.</p>
            <Link to="/flows/new" className="desk-tile desk-tile-create desk-tile-dashboard">
              Create your first desk
            </Link>
          </div>
        ) : (
          <div className="desk-button-row desk-dashboard-grid">
            {desks.map((desk) => (
              <DeskDashboardTile key={desk.path} desk={desk} runningCount={runsByDesk.get(desk.path) || 0} />
            ))}
            <Link to="/flows/new" className="desk-tile desk-tile-create desk-tile-dashboard">
              <span className="desk-tile-label">Create desk</span>
              <span className="desk-tile-role">New beat</span>
              <span className="desk-tile-meta">Start from Sports, Business, Tech, or AI News</span>
            </Link>
          </div>
        )}
      </section>

      <section className={`card dashboard-section${isWriting ? " is-writing" : ""}`}>
        <div className="dashboard-section-head">
          <div>
            <h3>{isWriting ? "Running now" : "Activity"}</h3>
            <p className="hint">
              {isWriting
                ? `${activeRuns.length} article${activeRuns.length === 1 ? "" : "s"} in progress.`
                : queuedCount > 0
                  ? `${queuedCount} topic${queuedCount === 1 ? "" : "s"} waiting for a puller.`
                  : "Nothing is running right now."}
            </p>
          </div>
          {(isWriting || queuedCount > 0) && (
            <Link to="/queue?tab=running" className="secondary">
              View active
            </Link>
          )}
        </div>
        {message && <p className="ok">{message}</p>}

        {isWriting ? (
          <div className="active-run-stack">
            {activeRuns.map((run) => (
              <ActiveRunCard
                key={run.run_id}
                run={run}
                busyRunId={stopRunId}
                deskLabel={run.flow_path ? deskLabelByPath.get(run.flow_path) : undefined}
                onStop={stopRun}
              />
            ))}
          </div>
        ) : (
          <p className="hint dashboard-idle-copy">
            Staff a shift on the shift board when you are ready to dispatch assignments.
          </p>
        )}
      </section>

      {issueChecks.length > 0 && (
        <section className="card dashboard-section dashboard-missing">
          <h3>Missing or blocked</h3>
          <p className="hint">Resolve these before running shifts.</p>
          <ReadinessChecklist checks={issueChecks} />
        </section>
      )}

      <section className="home-actions">
        <Link to="/shifts" className="home-action-card home-action-primary">
          <span className="home-action-label">Shift board</span>
          <span className="home-action-desc">Staff desks, activate windows, and complete shifts.</span>
          <span className="home-action-cta">Open shift board →</span>
        </Link>
        <Link to="/settings" className="home-action-card home-action-secondary">
          <span className="home-action-label">Integrations</span>
          <span className="home-action-desc">Control plane, Edition publish, and scheduler settings.</span>
          <span className="home-action-cta">Open settings →</span>
        </Link>
      </section>

      {articles.length > 0 && (
        <section className="card home-recent">
          <div className="home-recent-head">
            <h3>Recent artifacts</h3>
            <Link to="/articles" className="hint">
              View all →
            </Link>
          </div>
          <ul className="home-recent-list">
            {articles.map((article) => (
              <li key={article.run_id}>
                <Link to={`/articles/${article.run_id}`}>{article.title}</Link>
                {article.created_at && (
                  <span className="hint">
                    {new Date(article.created_at).toLocaleString(undefined, {
                      month: "short",
                      day: "numeric",
                      hour: "numeric",
                      minute: "2-digit",
                    })}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
