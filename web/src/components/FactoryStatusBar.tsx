import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type FactoryStatus } from "../api";

export default function FactoryStatusBar() {
  const [status, setStatus] = useState<FactoryStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => {
      void api
        .factoryStatus()
        .then((data) => {
          setStatus(data);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    };
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  if (error) {
    return (
      <div className="factory-status-bar error">
        Factory offline — <Link to="/">see home page</Link>
      </div>
    );
  }

  if (!status) {
    return <div className="factory-status-bar">Loading…</div>;
  }

  const readiness = status.readiness;
  const braveCheck = readiness.checks.find((c) => c.id === "brave_search");
  const active = status.active_run;

  return (
    <div className={`factory-status-bar phase-${readiness.phase}`}>
      <span className="factory-state-pill">{readiness.headline}</span>
      {status.state === "processing" && active && (
        <span className="factory-status-detail">
          {active.topic_prompt ?? active.run_id}
          {active.current_step ? ` · ${active.current_step}` : ""}
        </span>
      )}
      {readiness.phase === "needs_topics" && (
        <Link to="/queue" className="factory-status-link">Add topics</Link>
      )}
      {readiness.phase === "setup_required" && (
        <Link to="/settings" className="factory-status-link">Fix setup</Link>
      )}
      {braveCheck && !braveCheck.ok && (
        <Link to="/settings" className="factory-status-link">
          Configure Brave Search
        </Link>
      )}
    </div>
  );
}
