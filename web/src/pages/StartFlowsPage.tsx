import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import ModelSelectFields from "../components/ModelSelectFields";
import ShiftPlanDeskAccordion, { type ShiftPlanDeskDraft } from "../components/ShiftPlanDeskAccordion";
import { api, type FactorySettings, type QueuePresetSummary, type ShiftBoardWindow } from "../api";
import { loadDeskSummaries, type DeskSummary } from "../utils/desks";
import { readQueuePresetFile } from "../utils/parseTopicFile";
import { deskAssignmentCount } from "../utils/shiftPlanDesk";

function draftFromDeskSummary(desk: DeskSummary, planDesk?: ShiftPlanDeskDraft): ShiftPlanDeskDraft {
  return {
    desk_path: desk.path,
    display_name: desk.display_name || desk.path,
    topic_slug: planDesk?.topic_slug || desk.edition_topic_slug?.trim() || "general",
    topics: planDesk?.topics ? [...planDesk.topics] : [],
    reporter_selection_mode: planDesk?.reporter_selection_mode || "round_robin",
  };
}

export default function StartFlowsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const windowKey = searchParams.get("window_key") || "";

  const [boardWindow, setBoardWindow] = useState<ShiftBoardWindow | null>(null);
  const [settings, setSettings] = useState<FactorySettings | null>(null);
  const [presets, setPresets] = useState<QueuePresetSummary[]>([]);
  const [catalogDesks, setCatalogDesks] = useState<DeskSummary[]>([]);
  const [deskDrafts, setDeskDrafts] = useState<Record<string, ShiftPlanDeskDraft>>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedPresetSlug, setSelectedPresetSlug] = useState("");
  const presetFileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void api.getSettings().then(setSettings).catch((e: Error) => setError(e.message));
    void api.listQueuePresets().then((data) => setPresets(data.presets)).catch(() => undefined);
    void loadDeskSummaries(api.getFlowTree, api.listFlows)
      .then(setCatalogDesks)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!windowKey) {
      setError("Choose a shift from the shift board first.");
      return;
    }
    void api
      .getShiftBoard()
      .then((data) => {
        const match = data.windows.find((entry) => entry.window_key === windowKey) || null;
        setBoardWindow(match);
        if (!match) {
          setError("Unknown shift window.");
          return;
        }
        if (match.plan?.status === "active" || match.plan?.status === "complete") {
          setError("This shift can no longer be edited. Plan the next shift window instead.");
        }
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [windowKey]);

  useEffect(() => {
    if (catalogDesks.length === 0) {
      return;
    }
    const planByPath = new Map<string, ShiftPlanDeskDraft>();
    for (const slot of boardWindow?.plan?.desks || []) {
      planByPath.set(slot.desk_path, {
        desk_path: slot.desk_path,
        display_name: slot.name || slot.desk_path,
        topic_slug: slot.topic_slug,
        topics: (slot.assignments || []).map((assignment) => assignment.prompt),
        reporter_selection_mode: slot.reporter_selection_mode === "lru" ? "lru" : "round_robin",
      });
    }

    const next: Record<string, ShiftPlanDeskDraft> = {};
    for (const desk of catalogDesks) {
      next[desk.path] = draftFromDeskSummary(desk, planByPath.get(desk.path));
    }
    setDeskDrafts(next);
  }, [catalogDesks, boardWindow?.plan?.id, boardWindow?.plan?.desks]);

  const sortedDesks = useMemo(
    () =>
      catalogDesks
        .map((desk) => deskDrafts[desk.path])
        .filter((draft): draft is ShiftPlanDeskDraft => draft !== undefined)
        .sort((a, b) => a.display_name.localeCompare(b.display_name)),
    [catalogDesks, deskDrafts],
  );

  const summary = useMemo(() => {
    let empty = 0;
    let partial = 0;
    let ready = 0;
    for (const desk of sortedDesks) {
      const count = deskAssignmentCount(desk.topics);
      if (count === 0) {
        empty += 1;
      } else if (count < 3) {
        partial += 1;
      } else {
        ready += 1;
      }
    }
    return { empty, partial, ready, total: sortedDesks.length };
  }, [sortedDesks]);

  const updateDesk = (deskPath: string, patch: Partial<ShiftPlanDeskDraft>) => {
    setDeskDrafts((prev) => ({
      ...prev,
      [deskPath]: { ...prev[deskPath], ...patch },
    }));
  };

  const applyPreset = (preset: QueuePresetSummary & { topics?: string[] }) => {
    if (!preset.flow_path) {
      return;
    }
    setDeskDrafts((prev) => {
      const existing = prev[preset.flow_path];
      if (!existing) {
        return prev;
      }
      return {
        ...prev,
        [preset.flow_path]: {
          ...existing,
          topic_slug: preset.topic_slug || existing.topic_slug,
          topics: [...(preset.topics || [])],
        },
      };
    });
    if (preset.default_model && settings) {
      setSettings({ ...settings, default_model: preset.default_model });
    }
  };

  const savePlan = async (savePreset: boolean) => {
    if (!windowKey || !settings || !boardWindow) {
      return;
    }
    const model = settings.default_model.trim();
    if (!model) {
      setError("Select a model for this shift.");
      return;
    }

    const staffedDesks = sortedDesks
      .map((desk) => ({
        ...desk,
        topic_slug: desk.topic_slug.trim() || "general",
        topics: desk.topics.map((line) => line.trim()).filter(Boolean),
      }))
      .filter((desk) => desk.topics.length > 0);

    if (staffedDesks.length === 0) {
      setError("Add assignments to at least one desk before saving.");
      return;
    }

    const assignments_by_desk_index: Record<string, string[]> = {};
    staffedDesks.forEach((desk, index) => {
      assignments_by_desk_index[String(index)] = desk.topics;
    });

    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.saveShiftPlan({
        window_key: windowKey,
        default_model: model,
        desks: staffedDesks.map((desk) => ({
          desk_path: desk.desk_path,
          topic_slug: desk.topic_slug,
          name: desk.display_name,
          reporter_selection_mode: desk.reporter_selection_mode,
        })),
        assignments_by_desk_index,
        save_preset: savePreset,
        preset_name: boardWindow.label || "Shift roster",
      });
      setMessage(result.message);
      navigate("/shifts");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save shift plan.");
    } finally {
      setBusy(false);
    }
  };

  const shiftKey = boardWindow?.shift_key || "morning";
  const shiftLabel = boardWindow?.label || "Shift";

  return (
    <section className="card start-flows-page">
      <p>
        <Link to="/shifts">← Shift board</Link>
      </p>
      <h2>Plan a shift</h2>
      {boardWindow ? (
        <p className="hint">
          Staffing <strong>{boardWindow.label}</strong>. Expand each desk to generate or enter assignments, then save
          the plan and activate it from the shift board.
        </p>
      ) : (
        <p className="hint">Pick a shift window from the shift board to begin.</p>
      )}
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      {settings && boardWindow && (
        <>
          <div className="start-flows-model">
            <ModelSelectFields
              model={settings.default_model}
              onModelChange={(default_model) => setSettings({ ...settings, default_model })}
              label="Shift model"
              hint="Default model for assignments on this shift window."
              hidePullers
            />
          </div>

          <div className="shift-plan-legend" aria-label="Desk assignment status">
            <span className="shift-plan-legend-item">
              <span className="shift-plan-desk-status shift-plan-desk-status--empty" /> No assignments ({summary.empty})
            </span>
            <span className="shift-plan-legend-item">
              <span className="shift-plan-desk-status shift-plan-desk-status--partial" /> Under 3 ({summary.partial})
            </span>
            <span className="shift-plan-legend-item">
              <span className="shift-plan-desk-status shift-plan-desk-status--ready" /> 3+ ready ({summary.ready})
            </span>
          </div>

          {sortedDesks.length === 0 ? (
            <p className="hint">
              No desks found. A desk needs an Edition topic or beat brief — create one from the dashboard, not from
              pipeline templates.
            </p>
          ) : (
            <div className="shift-plan-desk-list">
              {sortedDesks.map((desk) => (
                <ShiftPlanDeskAccordion
                  key={desk.desk_path}
                  desk={desk}
                  shiftLabel={shiftLabel}
                  disabled={busy}
                  onChange={(patch) => updateDesk(desk.desk_path, patch)}
                  onGenerate={async (count) => {
                    const result = await api.generateDeskTopics({
                      desk_path: desk.desk_path,
                      shift_key: shiftKey,
                      count,
                    });
                    const topics = result.topics.map((line) => line.trim()).filter(Boolean);
                    if (topics.length === 0) {
                      throw new Error(result.warning || "No topics returned.");
                    }
                    updateDesk(desk.desk_path, { topics });
                  }}
                  onLoadStanding={async () => {
                    const data = await api.listStandingOrders(desk.desk_path);
                    const order = data.shifts.find((entry) => entry.shift_key === shiftKey);
                    const topics = (order?.topics || []).map((line) => line.trim()).filter(Boolean);
                    if (topics.length === 0) {
                      throw new Error(`No standing assignments saved for ${shiftLabel} on this desk.`);
                    }
                    updateDesk(desk.desk_path, { topics });
                  }}
                />
              ))}
            </div>
          )}

          {presets.length > 0 && (
            <div className="start-flows-presets-row">
              <label>
                Saved rosters
                <select value={selectedPresetSlug} onChange={(e) => setSelectedPresetSlug(e.target.value)}>
                  <option value="">Choose a saved roster…</option>
                  {presets.map((preset) => (
                    <option key={preset.slug} value={preset.slug}>
                      {preset.name} ({preset.topic_count} assignments)
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="secondary"
                disabled={!selectedPresetSlug || busy}
                onClick={() => {
                  const preset = presets.find((entry) => entry.slug === selectedPresetSlug);
                  if (!preset) {
                    return;
                  }
                  void api.getQueuePreset(preset.slug).then(({ preset: full }) => applyPreset(full));
                }}
              >
                Load roster
              </button>
            </div>
          )}

          <div className="start-flows-import-export">
            <label className="file-upload-button secondary">
              Import .queue.json
              <input
                ref={presetFileRef}
                type="file"
                accept=".json,.queue.json,application/json"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) {
                    return;
                  }
                  void readQueuePresetFile(file).then(applyPreset).catch((err: Error) => setError(err.message));
                }}
              />
            </label>
          </div>

          <div className="flow-queue-actions start-flows-composer-actions">
            <button type="button" className="primary" disabled={busy} onClick={() => savePlan(false)}>
              {busy ? "Saving…" : "Save shift plan"}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => savePlan(true)}>
              Save &amp; store roster preset
            </button>
          </div>
        </>
      )}
    </section>
  );
}
