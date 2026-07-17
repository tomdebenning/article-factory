import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ShiftBoardWindow } from "../api";

function statusLabel(window: ShiftBoardWindow): string {
  const plan = window.plan;
  if (!plan) {
    return "Not staffed";
  }
  if (plan.status === "active") {
    return "Active";
  }
  if (plan.status === "complete") {
    return "Complete";
  }
  if (plan.roster_review_status === "pending") {
    return "Review roster";
  }
  if (plan.roster_review_status === "ready") {
    return "Roster ready";
  }
  if (plan.assignment_total > 0) {
    return "Draft";
  }
  return "Not staffed";
}

function progressText(window: ShiftBoardWindow): string {
  const plan = window.plan;
  if (!plan || plan.assignment_total === 0) {
    return "No assignments yet";
  }
  const done = (plan.assignment_counts.completed || 0) + (plan.assignment_counts.failed || 0);
  return `${done}/${plan.assignment_total} finished · ${plan.assignment_counts.running || 0} running`;
}

export default function ShiftsBoardPage() {
  const [windows, setWindows] = useState<ShiftBoardWindow[]>([]);
  const [alerts, setAlerts] = useState<Awaited<ReturnType<typeof api.getShiftAlerts>>["alerts"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const reload = () => {
    void api
      .getShiftBoard()
      .then((data) => {
        setWindows(data.windows);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
    void api.getShiftAlerts().then((data) => setAlerts(data.alerts)).catch(() => setAlerts([]));
  };

  useEffect(() => {
    reload();
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, []);

  const activate = (planId: number) => {
    setBusyId(planId);
    setMessage(null);
    void api
      .activateShiftPlan(planId)
      .then((result) => {
        setMessage(result.message);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusyId(null));
  };

  const complete = (planId: number) => {
    setBusyId(planId);
    setMessage(null);
    void api
      .completeShiftPlan(planId)
      .then((result) => {
        setMessage(result.message);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusyId(null));
  };

  return (
    <section className="card shifts-board-page">
      <h2>Shift board</h2>
      <p className="hint">
        The next eight six-hour shifts in UTC — today and tomorrow. Shifts auto-activate at window boundaries when the
        scheduler is enabled in Settings; you can still activate or complete manually.
      </p>
      {alerts.length > 0 && (
        <ul className="newsroom-alerts">
          {alerts.map((alert) => (
            <li key={alert.id} className={`newsroom-alert severity-${alert.severity}`}>
              {alert.message}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      <div className="shifts-board-grid">
        {windows.map((window) => {
          const plan = window.plan;
          const canActivate = plan && plan.status === "draft" && plan.assignment_total > 0;
          const needsReview = plan?.roster_review_status === "pending";
          return (
            <article key={window.window_key} className="card shifts-board-card">
              <header className="shifts-board-card-head">
                <h3>{window.label}</h3>
                <span className={`queue-status-badge status-${plan?.status || "queued"}`}>
                  {statusLabel(window)}
                </span>
              </header>
              <p className="hint">{progressText(window)}</p>
              {needsReview && (
                <p className="ok">
                  Assignment Desk generated a roster — review before the shift goes live.
                </p>
              )}
              {plan && plan.desks.length > 0 && (
                <ul className="shifts-board-desks">
                  {plan.desks.map((desk) => (
                    <li key={desk.id}>
                      <strong>{desk.name || desk.desk_path}</strong>
                      <span className="hint">
                        {desk.assignment_total} assignment(s) · {desk.topic_slug}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="shifts-board-actions">
                {needsReview && plan && (
                  <Link to={`/shifts/review/${plan.id}`} className="primary">
                    Review roster
                  </Link>
                )}
                {plan?.status !== "complete" && (
                  <Link
                    to={`/start-flows?window_key=${encodeURIComponent(window.window_key)}`}
                    className="secondary"
                  >
                    {plan ? "Edit plan" : "Plan shift"}
                  </Link>
                )}
                {canActivate && (
                  <button
                    type="button"
                    className="primary"
                    disabled={busyId === plan.id}
                    onClick={() => activate(plan.id)}
                  >
                    {busyId === plan.id ? "Activating…" : "Activate shift"}
                  </button>
                )}
                {plan?.status === "active" && (
                  <>
                    <Link to="/queue?tab=running" className="secondary">
                      View active work
                    </Link>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busyId === plan.id}
                      onClick={() => complete(plan.id)}
                    >
                      {busyId === plan.id ? "Completing…" : "Complete shift"}
                    </button>
                  </>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
