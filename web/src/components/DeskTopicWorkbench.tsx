import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import TopicListEditor from "./TopicListEditor";
import { api, type Persona, type StandingOrderShift } from "../api";
import { DESK_SHIFT_KEYS, deskShiftLabel } from "../constants/shifts";
import { deskDetailUrl } from "../utils/desks";

type Props = {
  deskPath: string;
  editionTopicSlug: string;
  staffedPersonas: Persona[];
  shiftKey: string;
  onShiftKeyChange: (shiftKey: string) => void;
  standingOrders: Record<string, StandingOrderShift>;
  onStandingOrdersChanged: () => void;
  onQueueChanged?: () => void;
};

export default function DeskTopicWorkbench({
  deskPath,
  editionTopicSlug,
  staffedPersonas,
  shiftKey,
  onShiftKeyChange,
  standingOrders,
  onStandingOrdersChanged,
  onQueueChanged,
}: Props) {
  const [topicCount, setTopicCount] = useState("3");
  const [topics, setTopics] = useState<string[]>([]);
  const [reporterSlug, setReporterSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [queueingPrompt, setQueueingPrompt] = useState<string | null>(null);
  const [queueingAll, setQueueingAll] = useState(false);
  const topicsPanelRef = useRef<HTMLDivElement>(null);

  const cleanedTopics = topics.map((line) => line.trim()).filter(Boolean);
  const effectiveReporter = reporterSlug || staffedPersonas[0]?.slug || "";
  const topicSlug = editionTopicSlug.trim() || "general";
  const savedForShift = (standingOrders[shiftKey]?.topics || []).map((line) => line.trim()).filter(Boolean);

  useEffect(() => {
    setTopics(savedForShift.length > 0 ? [...savedForShift] : []);
    setError(null);
    setMessage(null);
  }, [shiftKey, savedForShift.join("\u0000")]);

  const selectShift = (key: string) => {
    onShiftKeyChange(key);
    setError(null);
    setMessage(null);
  };

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
      .generateDeskTopics({ desk_path: deskPath, shift_key: shiftKey, count })
      .then((result) => {
        const nextTopics = result.topics.map((line) => line.trim()).filter(Boolean);
        setTopics(nextTopics);
        if (nextTopics.length > 0) {
          setMessage(
            `Generated ${nextTopics.length} topic${nextTopics.length === 1 ? "" : "s"} for ${deskShiftLabel(shiftKey)}.`,
          );
          topicsPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } else {
          setError(
            result.warning ||
              "No topics returned — the model response could not be parsed. Try again or pick a different model.",
          );
        }
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setGenerating(false));
  };

  const loadSavedTopics = () => {
    if (savedForShift.length === 0) {
      setError(`No saved standing assignments for ${deskShiftLabel(shiftKey)} yet.`);
      return;
    }
    setTopics([...savedForShift]);
    setMessage(`Loaded ${savedForShift.length} saved assignment${savedForShift.length === 1 ? "" : "s"}.`);
    setError(null);
  };

  const saveToShift = (merge: boolean) => {
    if (cleanedTopics.length === 0) {
      setError("Add at least one topic before saving to a shift.");
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    void api
      .saveDeskTopics({
        desk_path: deskPath,
        shift_key: shiftKey,
        topics: cleanedTopics,
        merge,
      })
      .then(() => {
        setMessage(
          merge
            ? `Added ${cleanedTopics.length} topic${cleanedTopics.length === 1 ? "" : "s"} to ${deskShiftLabel(shiftKey)} standing assignments.`
            : `Saved ${cleanedTopics.length} topic${cleanedTopics.length === 1 ? "" : "s"} to ${deskShiftLabel(shiftKey)} standing assignments.`,
        );
        onStandingOrdersChanged();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSaving(false));
  };

  const queueTopic = async (prompt: string) => {
    const cleaned = prompt.trim();
    if (!cleaned) {
      return;
    }
    setQueueingPrompt(cleaned);
    setError(null);
    try {
      await api.enqueue(topicSlug, cleaned, deskPath);
      setMessage(`Queued topic for this desk. It will run when the puller is free.`);
      onQueueChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not queue topic.");
    } finally {
      setQueueingPrompt(null);
    }
  };

  const queueAllTopics = async () => {
    if (cleanedTopics.length === 0) {
      setError("Generate or enter topics before queuing.");
      return;
    }
    setQueueingAll(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.enqueueBatch(cleanedTopics, topicSlug, deskPath);
      setMessage(
        `Queued ${result.count ?? cleanedTopics.length} topic${cleanedTopics.length === 1 ? "" : "s"} on this desk.`,
      );
      onQueueChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not queue topics.");
    } finally {
      setQueueingAll(false);
    }
  };

  const runTopicNow = async (prompt: string) => {
    const cleaned = prompt.trim();
    if (!cleaned) {
      return;
    }
    setQueueingPrompt(cleaned);
    setError(null);
    try {
      const result = await api.startDeskTestRun({
        desk_path: deskPath,
        prompt: cleaned,
        topic_slug: topicSlug || undefined,
        reporter_persona_slug: effectiveReporter || undefined,
      });
      setMessage(`Started run ${result.run.run_id} immediately.`);
      onQueueChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start run.");
    } finally {
      setQueueingPrompt(null);
    }
  };

  const busy = generating || saving || queueingAll || Boolean(queueingPrompt);

  return (
    <div className="desk-topic-workbench">
      <p className="hint">
        Pick a shift, then <strong>Generate topics</strong> with AI (uses your beat brief). Save to the shift for
        recurring planning, or <strong>Queue all</strong> to run them on this desk.
      </p>

      <h4 className="desk-subsection-title">Shift</h4>
      <div className="desk-button-row desk-shift-tabs">
        {DESK_SHIFT_KEYS.map((shift) => {
          const count = standingOrders[shift.key]?.topics?.filter(Boolean).length ?? 0;
          return (
            <button
              key={shift.key}
              type="button"
              className={`desk-tile desk-tile-compact${shiftKey === shift.key ? " is-active" : ""}`}
              onClick={() => selectShift(shift.key)}
            >
              {shift.label}
              {count > 0 ? ` (${count} saved)` : ""}
            </button>
          );
        })}
      </div>

      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      <div className="desk-topic-generate-panel">
        <h4 className="desk-subsection-title">Generate for {deskShiftLabel(shiftKey)}</h4>
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
          {staffedPersonas.length > 0 && (
            <label>
              Staff persona for immediate runs
              <select value={effectiveReporter} onChange={(e) => setReporterSlug(e.target.value)}>
                {staffedPersonas.map((persona) => (
                  <option key={persona.slug} value={persona.slug}>
                    {persona.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="desk-page-actions">
          <button type="button" className="primary" disabled={busy} onClick={generateTopics}>
            {generating ? "Generating…" : "Generate topics"}
          </button>
          <button type="button" className="secondary" disabled={busy || savedForShift.length === 0} onClick={loadSavedTopics}>
            Load saved assignments
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || cleanedTopics.length === 0}
            onClick={() => void queueAllTopics()}
          >
            {queueingAll ? "Queueing…" : "Queue all"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy || cleanedTopics.length === 0}
            onClick={() => saveToShift(false)}
          >
            {saving ? "Saving…" : `Save to ${deskShiftLabel(shiftKey)}`}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy || cleanedTopics.length === 0}
            onClick={() => saveToShift(true)}
          >
            Append to {deskShiftLabel(shiftKey)}
          </button>
          <Link to={`/desks/shift?path=${encodeURIComponent(deskPath)}&shift=${encodeURIComponent(shiftKey)}`} className="secondary">
            Edit target count
          </Link>
        </div>
      </div>

      {staffedPersonas.length === 0 && (
        <p className="hint">
          Assign at least one staff persona on the <Link to={deskDetailUrl(deskPath, { tab: "config" })}>Config</Link>{" "}
          tab before starting immediate runs.
        </p>
      )}

      <div ref={topicsPanelRef}>
        <TopicListEditor
          topics={topics}
          onChange={setTopics}
          disabled={busy}
          label={`Topics for ${deskShiftLabel(shiftKey)}`}
          countLabel="topic"
          emptyLabel="No topics yet — click Generate topics above."
        />
      </div>

      {cleanedTopics.length > 0 && (
        <ul className="desk-topic-run-list">
          {cleanedTopics.map((prompt) => (
            <li key={prompt} className="desk-topic-run-item">
              <span>{prompt}</span>
              <div className="desk-topic-run-actions">
                <button type="button" className="primary" disabled={busy} onClick={() => void queueTopic(prompt)}>
                  {queueingPrompt === prompt ? "Queueing…" : "Queue"}
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || staffedPersonas.length === 0}
                  onClick={() => void runTopicNow(prompt)}
                >
                  Run now
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
