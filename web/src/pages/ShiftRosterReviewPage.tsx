import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ShiftPlanSummary } from "../api";

type AssignmentDraft = {
  id: number;
  prompt: string;
  source: string;
  locked: boolean;
  desk_name: string;
};

export default function ShiftRosterReviewPage() {
  const { planId } = useParams();
  const navigate = useNavigate();
  const numericId = Number(planId);
  const [plan, setPlan] = useState<ShiftPlanSummary | null>(null);
  const [assignments, setAssignments] = useState<AssignmentDraft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    if (!Number.isFinite(numericId)) {
      return;
    }
    void api
      .getShiftPlan(numericId)
      .then((data) => {
        setPlan(data.plan);
        const rows: AssignmentDraft[] = [];
        for (const desk of data.plan.desks) {
          for (const assignment of desk.assignments || []) {
            rows.push({
              id: assignment.id,
              prompt: assignment.prompt,
              source: assignment.source || "manual",
              locked: Boolean(assignment.locked),
              desk_name: desk.name || desk.desk_path,
            });
          }
        }
        setAssignments(rows);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    reload();
  }, [numericId]);

  const aiCount = useMemo(
    () => assignments.filter((row) => row.source === "ai_suggested").length,
    [assignments],
  );

  const saveDraft = async () => {
    if (!Number.isFinite(numericId)) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.patchShiftRoster(numericId, {
        assignments: assignments.map((row) => ({
          id: row.id,
          prompt: row.prompt,
          locked: row.locked,
        })),
      });
      setPlan(result.plan);
      setMessage("Roster updated.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const approveAll = async () => {
    if (!Number.isFinite(numericId)) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await api.patchShiftRoster(numericId, {
        assignments: assignments.map((row) => ({
          id: row.id,
          prompt: row.prompt,
          locked: row.locked,
        })),
      });
      const result = await api.approveShiftRoster(numericId);
      setPlan(result.plan);
      setMessage(result.message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Approve failed");
    } finally {
      setBusy(false);
    }
  };

  const rejectAi = async () => {
    if (!Number.isFinite(numericId)) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.rejectShiftRosterAi(numericId);
      setPlan(result.plan);
      setMessage(result.message);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reject failed");
    } finally {
      setBusy(false);
    }
  };

  if (!Number.isFinite(numericId)) {
    return (
      <section className="card">
        <p className="error">Invalid shift plan.</p>
        <Link to="/shifts">Back to shift board</Link>
      </section>
    );
  }

  if (!plan) {
    return (
      <section className="card">
        <p>Loading roster…</p>
      </section>
    );
  }

  return (
    <section className="card shift-roster-review-page">
      <p>
        <Link to="/shifts">← Shift board</Link>
      </p>
      <h2>Review roster</h2>
      <p className="hint">
        {plan.shift_key} shift · {plan.roster_review_status === "ready" ? "Approved" : "Awaiting review"}
        {plan.roster_generated_at ? ` · Generated ${new Date(plan.roster_generated_at).toLocaleString()}` : ""}
      </p>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      {assignments.length === 0 ? (
        <p className="hint">No assignments on this shift yet.</p>
      ) : (
        <ul className="shift-roster-review-list">
          {assignments.map((row, index) => (
            <li key={row.id} className="shift-roster-review-row">
              <div className="shift-roster-review-meta">
                <strong>{row.desk_name}</strong>
                <span className={`queue-status-badge source-${row.source}`}>{row.source.replace("_", " ")}</span>
              </div>
              <textarea
                value={row.prompt}
                rows={2}
                onChange={(e) =>
                  setAssignments((prev) =>
                    prev.map((item, i) => (i === index ? { ...item, prompt: e.target.value } : item)),
                  )
                }
              />
              <label className="shift-roster-lock-label">
                <input
                  type="checkbox"
                  checked={row.locked}
                  onChange={(e) =>
                    setAssignments((prev) =>
                      prev.map((item, i) => (i === index ? { ...item, locked: e.target.checked } : item)),
                    )
                  }
                />
                Lock (preserve at next T-15)
              </label>
            </li>
          ))}
        </ul>
      )}

      <div className="shift-roster-review-actions">
        <button type="button" className="secondary" disabled={busy} onClick={() => void saveDraft()}>
          Save edits
        </button>
        {aiCount > 0 && (
          <button type="button" className="secondary" disabled={busy} onClick={() => void rejectAi()}>
            Reject AI only ({aiCount})
          </button>
        )}
        <button type="button" className="primary" disabled={busy || plan.roster_review_status === "ready"} onClick={() => void approveAll()}>
          Approve roster
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={() => navigate("/shifts")}>
          Back to board
        </button>
      </div>
    </section>
  );
}
