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
  const [topicCount, setTopicCount] = useState("3");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);

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

  const generateTopics = () => {
    const count = Number(topicCount);
    if (!Number.isFinite(count) || count < 1 || count > 20) {
      setError("Topic count must be between 1 and 20.");
      return;
    }
    setGenerating(true);
    setError(null);
    setMessage(null);
    void api
      .generateDeskTopics({ desk_path: path, shift_key: shiftKey, count })
      .then((result) => {
        const nextTopics = result.topics.map((line) => line.trim()).filter(Boolean);
        if (nextTopics.length === 0) {
          setError(result.warning || "No topics returned.");
          return;
        }
        setTopics(nextTopics);
        setMessage(`Generated ${nextTopics.length} topics — save shift config to keep them.`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setGenerating(false));
  };

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
        <Link to={deskDetailUrl(path, { tab: "queue", shift: shiftKey })}>Desk queue</Link>
        {" · Shift config"}
      </p>
      <h2>{deskShiftLabel(shiftKey)} shift — standing assignments</h2>
      <p className="hint">
        Manual edit for recurring assignments and target count. To <strong>generate topics with AI</strong> and queue
        runs, use{" "}
        <Link to={deskDetailUrl(path, { tab: "queue", shift: shiftKey })}>Current Queue</Link> on the desk page.
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

      <div className="desk-topic-generate-panel">
        <h4 className="desk-subsection-title">Generate with AI</h4>
        <div className="desk-topic-workbench-controls">
          <label>
            Topic count
            <input
              type="number"
              min={1}
              max={20}
              value={topicCount}
              onChange={(e) => setTopicCount(e.target.value)}
            />
          </label>
        </div>
        <div className="desk-page-actions">
          <button type="button" className="primary" disabled={generating || saving} onClick={generateTopics}>
            {generating ? "Generating…" : "Generate topics"}
          </button>
          <Link to={deskDetailUrl(path, { tab: "queue", shift: shiftKey })} className="secondary">
            Open Current Queue
          </Link>
        </div>
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
      <TopicListEditor
        topics={topics.map((line) => line.trim()).filter(Boolean)}
        onChange={(next) => setTopics(next.length > 0 ? next : [""])}
        label="Standing assignments"
        countLabel="standing assignment"
        emptyLabel="No standing assignments yet. Generate above or add manually."
      />
      {order?.updated_at && <p className="hint">Last saved {new Date(order.updated_at).toLocaleString()}.</p>}
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}
      <div className="desk-page-actions">
        <button type="button" className="primary" disabled={saving || generating} onClick={save}>
          {saving ? "Saving…" : "Save shift config"}
        </button>
        <Link to={deskDetailUrl(path, { tab: "review", shift: shiftKey })} className="secondary">
          Back to desk review
        </Link>
      </div>
    </section>
  );
}
