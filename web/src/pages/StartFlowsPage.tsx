import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import ModelSelectFields from "../components/ModelSelectFields";
import TopicListEditor from "../components/TopicListEditor";
import { api, type FactorySettings, type QueuePresetSummary, type ShiftBoardWindow } from "../api";
import { useFlowSelectOptions } from "../hooks/useFlowSelectOptions";
import { readQueuePresetFile } from "../utils/parseTopicFile";
import { ensureFlowSelectOption } from "../utils/flowSelectOptions";

type DeskDraft = {
  name: string;
  desk_path: string;
  topic_slug: string;
  topics: string[];
};

const emptyDesk = (deskPath = ""): DeskDraft => ({
  name: "",
  desk_path: deskPath,
  topic_slug: "general",
  topics: [],
});

export default function StartFlowsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const windowKey = searchParams.get("window_key") || "";

  const [boardWindow, setBoardWindow] = useState<ShiftBoardWindow | null>(null);
  const [settings, setSettings] = useState<FactorySettings | null>(null);
  const [presets, setPresets] = useState<QueuePresetSummary[]>([]);
  const { options: flowOptions, loading: flowsLoading } = useFlowSelectOptions();
  const [desks, setDesks] = useState<DeskDraft[]>([emptyDesk()]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedPresetSlug, setSelectedPresetSlug] = useState("");
  const presetFileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void api.getSettings().then(setSettings).catch((e: Error) => setError(e.message));
    void api.listQueuePresets().then((data) => setPresets(data.presets)).catch(() => undefined);
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
        if (match.plan) {
          setDesks(
            match.plan.desks.length > 0
              ? match.plan.desks.map((desk) => ({
                  name: desk.name,
                  desk_path: desk.desk_path,
                  topic_slug: desk.topic_slug,
                  topics: (desk.assignments || []).map((assignment) => assignment.prompt),
                }))
              : [emptyDesk(settings?.default_flow_path || "")],
          );
        } else if (settings?.default_flow_path) {
          setDesks([emptyDesk(settings.default_flow_path)]);
        }
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [windowKey, settings?.default_flow_path]);

  const flowSelectOptions = useMemo(
    () =>
      ensureFlowSelectOption(
        flowOptions,
        desks[0]?.desk_path || settings?.default_flow_path || "",
      ),
    [desks, flowOptions, settings?.default_flow_path],
  );

  const updateDesk = (index: number, patch: Partial<DeskDraft>) => {
    setDesks((prev) => prev.map((desk, i) => (i === index ? { ...desk, ...patch } : desk)));
  };

  const addDesk = () => {
    setDesks((prev) => [...prev, emptyDesk(settings?.default_flow_path || "")]);
  };

  const removeDesk = (index: number) => {
    setDesks((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
  };

  const applyPreset = (preset: QueuePresetSummary & { topics?: string[] }) => {
    setDesks([
      {
        name: preset.name,
        desk_path: preset.flow_path,
        topic_slug: preset.topic_slug,
        topics: [...(preset.topics || [])],
      },
    ]);
    if (preset.default_model && settings) {
      setSettings({ ...settings, default_model: preset.default_model });
    }
  };

  const savePlan = async (savePreset: boolean) => {
    if (!windowKey || !settings) {
      return;
    }
    const model = settings.default_model.trim();
    if (!model) {
      setError("Select a model for this shift.");
      return;
    }
    const cleanedDesks = desks
      .map((desk) => ({
        ...desk,
        desk_path: desk.desk_path.trim(),
        topic_slug: desk.topic_slug.trim() || "general",
        topics: desk.topics.map((line) => line.trim()).filter(Boolean),
      }))
      .filter((desk) => desk.desk_path);
    if (cleanedDesks.length === 0) {
      setError("Add at least one desk.");
      return;
    }
    const assignments_by_desk_index: Record<string, string[]> = {};
    cleanedDesks.forEach((desk, index) => {
      assignments_by_desk_index[String(index)] = desk.topics;
    });
    const totalAssignments = cleanedDesks.reduce((sum, desk) => sum + desk.topics.length, 0);
    if (totalAssignments < 1) {
      setError("Add at least one assignment across the staffed desks.");
      return;
    }

    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.saveShiftPlan({
        window_key: windowKey,
        default_model: model,
        desks: cleanedDesks.map((desk) => ({
          desk_path: desk.desk_path,
          topic_slug: desk.topic_slug,
          name: desk.name.trim() || desk.desk_path,
        })),
        assignments_by_desk_index,
        save_preset: savePreset,
        preset_name: cleanedDesks[0]?.name || boardWindow?.label || "Shift roster",
      });
      setMessage(result.message);
      navigate("/shifts");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save shift plan.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card start-flows-page">
      <p>
        <Link to="/shifts">← Shift board</Link>
      </p>
      <h2>Plan a shift</h2>
      {boardWindow ? (
        <p className="hint">
          Staffing <strong>{boardWindow.label}</strong>. Save the plan, then activate it from the shift board.
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
            />
          </div>

          {desks.map((desk, index) => (
            <div key={index} className="step-card shift-desk-card">
              <div className="shift-desk-card-head">
                <h3>Desk {index + 1}</h3>
                {desks.length > 1 && (
                  <button type="button" className="secondary" onClick={() => removeDesk(index)}>
                    Remove desk
                  </button>
                )}
              </div>
              <div className="start-flows-composer-grid">
                <label>
                  Desk name
                  <input
                    value={desk.name}
                    onChange={(e) => updateDesk(index, { name: e.target.value })}
                    placeholder="Sports desk"
                  />
                </label>
                <label>
                  Edition topic
                  <input
                    value={desk.topic_slug}
                    onChange={(e) => updateDesk(index, { topic_slug: e.target.value })}
                    placeholder="sports"
                  />
                </label>
                <label>
                  Assigned desk
                  <select
                    value={desk.desk_path}
                    disabled={flowsLoading}
                    onChange={(e) => updateDesk(index, { desk_path: e.target.value })}
                  >
                    {!desk.desk_path && <option value="">Choose a desk…</option>}
                    {flowSelectOptions.map((option) => (
                      <option key={option.path} value={option.path}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <TopicListEditor
                topics={desk.topics}
                onChange={(topics) => updateDesk(index, { topics })}
                disabled={busy}
              />
            </div>
          ))}

          <div className="flow-queue-actions start-flows-composer-actions">
            <button type="button" className="secondary" onClick={addDesk}>
              Add another desk
            </button>
          </div>

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
