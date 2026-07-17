import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import TopicListEditor from "../components/TopicListEditor";
import { api, type StandingOrderShift } from "../api";
import { deskShiftLabel } from "../constants/shifts";
import { deskDetailUrl, isTemplateFlowPath } from "../utils/desks";

export default function DeskShiftPage() {
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path")?.trim() || "";
  const shiftKey = searchParams.get("shift")?.trim() || "morning";

  const [order, setOrder] = useState<StandingOrderShift | null>(null);
  const [topics, setTopics] = useState<string[]>([""]);
  const [targetCount, setTargetCount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!path || isTemplateFlowPath(path)) {
      return;
    }
    void api
      .listStandingOrders(path)
      .then((data) => {
        const match = data.shifts.find((shift) => shift.shift_key === shiftKey) || null;
        setOrder(match);
        setTopics(match?.topics?.length ? [...match.topics] : [""]);
        setTargetCount(match?.target_count != null ? String(match.target_count) : "");
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [path, shiftKey]);

  const save = () => {
    setSaving(true);
    setMessage(null);
    setError(null);
    const parsedTarget = targetCount.trim() === "" ? null : Number(targetCount);
    if (parsedTarget != null && (!Number.isFinite(parsedTarget) || parsedTarget < 0)) {
      setError("Target count must be zero or greater.");
      setSaving(false);
      return;
    }
    void api
      .saveStandingOrder({
        desk_path: path,
        shift_key: shiftKey,
        topics: topics.map((line) => line.trim()).filter(Boolean),
        target_count: parsedTarget,
      })
      .then((result) => {
        setOrder(result.order);
        setMessage(`Saved ${deskShiftLabel(shiftKey)} shift config for this desk.`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSaving(false));
  };

  if (!path) {
    return (
      <section className="card">
        <h2>Shift config</h2>
        <p className="hint">Open a desk first, then choose a shift.</p>
        <Link to="/" className="secondary">
          Back to dashboard
        </Link>
      </section>
    );
  }

  return (
    <section className="card desk-shift-page">
      <p className="home-eyebrow">
        <Link to="/">Dashboard</Link>
        {" · "}
        <Link to={deskDetailUrl(path)}>Desk</Link>
        {" · Shift"}
      </p>
      <h2>
        {deskShiftLabel(shiftKey)} shift
      </h2>
      <p className="hint">
        Standing orders for <code>{path}</code>. At T-15 before shift start, these topics auto-fill the roster; AI
        suggests any remaining slots up to the target count.
      </p>

      <div className="desk-button-row desk-shift-tabs">
        {(["night", "morning", "afternoon", "evening"] as const).map((key) => (
          <Link
            key={key}
            to={`/desks/shift?path=${encodeURIComponent(path)}&shift=${key}`}
            className={`desk-tile desk-tile-compact${shiftKey === key ? " is-active" : ""}`}
          >
            {deskShiftLabel(key)}
          </Link>
        ))}
      </div>

      <label>
        Target count (total assignments for this desk on this shift)
        <input
          type="number"
          min={0}
          value={targetCount}
          onChange={(e) => setTargetCount(e.target.value)}
          placeholder="Defaults to topic list length"
        />
      </label>
      <TopicListEditor topics={topics} onChange={setTopics} label="Standing topics" />
      {order?.updated_at && (
        <p className="hint">Last saved {new Date(order.updated_at).toLocaleString()}.</p>
      )}
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}
      <div className="desk-page-actions">
        <button type="button" className="primary" disabled={saving} onClick={save}>
          {saving ? "Saving…" : "Save shift config"}
        </button>
        <Link to={deskDetailUrl(path)} className="secondary">
          Back to desk
        </Link>
      </div>
    </section>
  );
}
