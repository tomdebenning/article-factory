import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type FlowPerformanceData,
  type FlowPerformanceRun,
  type StandingOrderShift,
} from "../api";
import { DESK_SHIFT_KEYS, deskShiftLabel } from "../constants/shifts";
import { completionRate, firstPassYieldRate, num, pct } from "../utils/flowMetrics";
import { deskShiftUrl } from "../utils/desks";

type Props = {
  deskPath: string;
  standingOrders: Record<string, StandingOrderShift>;
};

function runTopicLabel(run: FlowPerformanceRun): string {
  const prompt = run.topic_prompt?.trim();
  if (prompt) {
    return prompt.length > 120 ? `${prompt.slice(0, 117)}…` : prompt;
  }
  return run.topic_slug || run.run_id;
}

export default function DeskReviewPanel({ deskPath, standingOrders }: Props) {
  const [shiftKey, setShiftKey] = useState<string>("morning");
  const [performance, setPerformance] = useState<FlowPerformanceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    void api
      .getFlowPerformance(deskPath)
      .then(setPerformance)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deskPath]);

  const shiftOrder = standingOrders[shiftKey];
  const standingTopics = (shiftOrder?.topics || []).map((line) => line.trim()).filter(Boolean);

  const runs = performance?.runs ?? [];
  const overall = performance?.overall;

  const topicStats = useMemo(() => {
    return standingTopics.map((topic) => {
      const related = runs.filter((run) => {
        const prompt = run.topic_prompt?.trim() || "";
        return prompt === topic || prompt.startsWith(topic.slice(0, 40));
      });
      const completed = related.filter((run) => run.status === "completed").length;
      const failed = related.filter((run) => run.status === "failed").length;
      const active = related.filter((run) => run.status === "running").length;
      return { topic, related, completed, failed, active };
    });
  }, [runs, standingTopics]);

  return (
    <div className="desk-review-panel">
      <p className="hint">
        Review standing assignments and how this desk&apos;s topics have performed. Shift tabs show recurring
        assignments; statistics cover all runs on this desk pipeline.
      </p>

      <div className="desk-button-row desk-shift-tabs">
        {DESK_SHIFT_KEYS.map((shift) => {
          const order = standingOrders[shift.key];
          const count = order?.topics?.filter(Boolean).length ?? 0;
          return (
            <button
              key={shift.key}
              type="button"
              className={`desk-tile desk-tile-compact${shiftKey === shift.key ? " is-active" : ""}`}
              onClick={() => setShiftKey(shift.key)}
            >
              {shift.label}
              {count > 0 ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>

      <section className="desk-review-section">
        <div className="desk-section-head">
          <h4 className="desk-subsection-title">{deskShiftLabel(shiftKey)} standing assignments</h4>
          <Link to={deskShiftUrl(deskPath, shiftKey)} className="secondary">
            Edit shift config
          </Link>
        </div>
        {standingTopics.length === 0 ? (
          <p className="hint desk-queue-empty">
            No standing assignments for {deskShiftLabel(shiftKey)} yet. Add them on the shift config page or from
            Current Queue.
          </p>
        ) : (
          <ul className="desk-review-topic-list">
            {topicStats.map(({ topic, completed, failed, active, related }) => (
              <li key={topic} className="desk-review-topic-item">
                <p className="desk-review-topic-prompt">{topic}</p>
                <p className="desk-review-topic-meta hint">
                  {related.length === 0
                    ? "No runs yet"
                    : `${completed} completed · ${failed} failed · ${active} running`}
                  {shiftOrder?.target_count != null ? ` · target ${shiftOrder.target_count}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="desk-review-section">
        <div className="desk-section-head">
          <h4 className="desk-subsection-title">Desk statistics</h4>
          <Link to={`/flows/performance?path=${encodeURIComponent(deskPath)}`} className="secondary">
            Full performance
          </Link>
        </div>

        {loading && <p className="hint">Loading statistics…</p>}
        {error && <p className="error">{error}</p>}

        {overall && !loading && (
          <div className="desk-review-kpis">
            <div className="desk-review-kpi">
              <span className="desk-review-kpi-label">Runs</span>
              <strong>{overall.run_count ?? 0}</strong>
            </div>
            <div className="desk-review-kpi">
              <span className="desk-review-kpi-label">Completion</span>
              <strong>{pct(completionRate(overall))}</strong>
            </div>
            <div className="desk-review-kpi">
              <span className="desk-review-kpi-label">First-pass yield</span>
              <strong>{pct(firstPassYieldRate(overall))}</strong>
            </div>
            <div className="desk-review-kpi">
              <span className="desk-review-kpi-label">Review cycles</span>
              <strong>{num(overall.median_review_rounds, 0)}</strong>
            </div>
            <div className="desk-review-kpi">
              <span className="desk-review-kpi-label">Failures</span>
              <strong>{overall.failure_count ?? 0}</strong>
            </div>
          </div>
        )}

        {runs.length > 0 && (
          <div className="stats-table-wrap desk-review-table-wrap">
            <table className="stats-table stats-table-recent">
              <thead>
                <tr>
                  <th scope="col">Topic</th>
                  <th scope="col">Status</th>
                  <th scope="col">Drafts</th>
                  <th scope="col">Reviews</th>
                  <th scope="col">First pass</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 25).map((run) => (
                  <tr key={run.run_id}>
                    <td>{runTopicLabel(run)}</td>
                    <td>{run.status}</td>
                    <td>{run.draft_number ?? "—"}</td>
                    <td>{run.review_rounds ?? run.review_round ?? "—"}</td>
                    <td>
                      {run.first_pass_accept == null ? "—" : run.first_pass_accept ? "Yes" : "No"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
