import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import RunProgressPanel from "../components/RunProgressPanel";
import {
  api,
  QUEUE_STATUS_LABEL,
  type CompletedArticle,
  type FactoryStatus,
  type ReadinessCheck,
  type RunSummary,
} from "../api";

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
  onStop,
}: {
  run: RunSummary;
  busyRunId: string | null;
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
              <Link to={`/flows/edit?path=${encodeURIComponent(run.flow_path)}`}>{run.flow_path}</Link>
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

export default function DashboardPage() {
  const [status, setStatus] = useState<FactoryStatus | null>(null);
  const [articles, setArticles] = useState<CompletedArticle[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [stopRunId, setStopRunId] = useState<string | null>(null);

  const reload = () => {
    void Promise.all([api.factoryStatus(), api.listArticles()])
      .then(([nextStatus, articleList]) => {
        setStatus(nextStatus);
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
        <p className="hint">Loading…</p>
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

  return (
    <div className="home-page">
      <section className="card home-hero">
        <p className="home-eyebrow">Welcome</p>
        <p className="home-lead">
          Turn a list of topics into finished articles using reusable prompt flows. Assign a model,
          queue your topics, and the factory runs each step through your control-plane pullers —
          then publish accepted artifacts to Showroom.
        </p>
      </section>

      <section className={`card home-activity${isWriting ? " is-writing" : ""}`}>
        <div className="home-activity-head">
          <div>
            <h3>{isWriting ? "Writing now" : "Activity"}</h3>
            <p className="hint home-activity-sub">
              {isWriting
                ? `${activeRuns.length} article${activeRuns.length === 1 ? "" : "s"} in progress right now.`
                : queuedCount > 0
                  ? `${queuedCount} topic${queuedCount === 1 ? "" : "s"} queued — waiting for an idle puller.`
                  : "Nothing is running at the moment. Start a flow when you are ready."}
            </p>
          </div>
          {(isWriting || queuedCount > 0) && (
            <Link to="/queue?tab=running" className="secondary home-activity-link">
              View Active
            </Link>
          )}
        </div>
        {message && <p className="ok">{message}</p>}

        {isWriting ? (
          <div className="active-run-stack">
            {activeRuns.map((run) => (
              <ActiveRunCard key={run.run_id} run={run} busyRunId={stopRunId} onStop={stopRun} />
            ))}
          </div>
        ) : (
          <div className="home-idle-panel">
            <p>
              {readiness.phase === "setup_required"
                ? "Finish setup below, then queue topics to begin."
                : "Pick an existing flow to run, or create a new one in the flow library."}
            </p>
            {!readiness.setup_complete && (
              <p className="hint">{readiness.summary}</p>
            )}
          </div>
        )}
      </section>

      <section className="home-actions">
        <Link to="/start-flows" className="home-action-card home-action-primary">
          <span className="home-action-label">Start a flow</span>
          <span className="home-action-desc">
            Name a queue, choose a flow and model, add topics, and start writing.
          </span>
          <span className="home-action-cta">Go to Start flows →</span>
        </Link>
        <Link to="/flows/new" className="home-action-card home-action-secondary">
          <span className="home-action-label">Create a new flow</span>
          <span className="home-action-desc">
            Build a new prompt pipeline from a template or blank step count.
          </span>
          <span className="home-action-cta">Open flow creator →</span>
        </Link>
      </section>

      {issueChecks.length > 0 && (
        <section className="card home-setup">
          <h3>Before you run</h3>
          <p className="hint">These items should be resolved for reliable article production.</p>
          <ReadinessChecklist checks={issueChecks} />
        </section>
      )}

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
