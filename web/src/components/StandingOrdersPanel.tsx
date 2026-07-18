import { useEffect, useState } from "react";
import TopicListEditor from "./TopicListEditor";
import { api, type StandingOrderShift } from "../api";

const SHIFT_TABS = [
  { key: "night", label: "Night" },
  { key: "morning", label: "Morning" },
  { key: "afternoon", label: "Afternoon" },
  { key: "evening", label: "Evening" },
] as const;

type Props = {
  deskPath: string;
};

export default function StandingOrdersPanel({ deskPath }: Props) {
  const [activeShift, setActiveShift] = useState<string>("morning");
  const [orders, setOrders] = useState<Record<string, StandingOrderShift>>({});
  const [topics, setTopics] = useState<string[]>([]);
  const [targetCount, setTargetCount] = useState<string>("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!deskPath) {
      return;
    }
    void api
      .listStandingOrders(deskPath)
      .then((data) => {
        const map: Record<string, StandingOrderShift> = {};
        for (const shift of data.shifts) {
          map[shift.shift_key] = shift;
        }
        setOrders(map);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [deskPath]);

  useEffect(() => {
    const order = orders[activeShift];
    setTopics(order?.topics?.length ? [...order.topics] : [""]);
    setTargetCount(order?.target_count != null ? String(order.target_count) : "");
  }, [activeShift, orders]);

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
        desk_path: deskPath,
        shift_key: activeShift,
        topics: topics.map((line) => line.trim()).filter(Boolean),
        target_count: parsedTarget,
      })
      .then((result) => {
        setOrders((prev) => ({ ...prev, [activeShift]: result.order }));
        setMessage(`Standing order saved for ${activeShift} shift.`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSaving(false));
  };

  return (
    <div className="step-card flow-standing-orders-card">
      <h3>Standing orders</h3>
      <p className="hint">
        Recurring assignment templates per shift. At T-15 before shift start, these story angles auto-fill the roster;
        AI suggests any remaining slots up to the target count.
      </p>
      <div className="flow-standing-orders-tabs">
        {SHIFT_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={activeShift === tab.key ? "primary" : "secondary"}
            onClick={() => setActiveShift(tab.key)}
          >
            {tab.label}
          </button>
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
      <TopicListEditor
        topics={topics}
        onChange={setTopics}
        label="Standing assignments"
        countLabel="standing assignment"
      />
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}
      <button type="button" className="secondary" disabled={saving} onClick={save}>
        {saving ? "Saving…" : "Save standing order"}
      </button>
    </div>
  );
}
