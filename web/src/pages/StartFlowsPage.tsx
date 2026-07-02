import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import ModelSelectFields from "../components/ModelSelectFields";
import TopicListEditor from "../components/TopicListEditor";
import {
  api,
  DEFAULT_FLOW_PATH,
  type FactorySettings,
  type QueuePresetSummary,
} from "../api";
import { useFlowSelectOptions } from "../hooks/useFlowSelectOptions";
import {
  downloadQueuePresetFile,
  readQueuePresetFile,
} from "../utils/parseTopicFile";
import { ensureFlowSelectOption } from "../utils/flowSelectOptions";
import { resolveComposerFlowPath } from "../utils/flowFiles";

type ComposerState = {
  name: string;
  flow_path: string;
  topic_slug: string;
  topics: string[];
  presetSlug: string;
};

const emptyComposer = (flowPath: string): ComposerState => ({
  name: "",
  flow_path: flowPath,
  topic_slug: "general",
  topics: [],
  presetSlug: "",
});

export default function StartFlowsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [settings, setSettings] = useState<FactorySettings | null>(null);
  const [presets, setPresets] = useState<QueuePresetSummary[]>([]);
  const { options: flowOptions, loading: flowsLoading, reload: reloadFlowOptions } = useFlowSelectOptions();
  const [composer, setComposer] = useState<ComposerState>(() => emptyComposer(""));
  const [composerReady, setComposerReady] = useState(false);
  const [selectedPresetSlug, setSelectedPresetSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [composerBusy, setComposerBusy] = useState(false);
  const presetFileRef = useRef<HTMLInputElement>(null);

  const reloadPresets = () => {
    void api
      .listQueuePresets()
      .then((data) => setPresets(data.presets))
      .catch(() => {
        /* presets optional */
      });
  };

  useEffect(() => {
    void api
      .getSettings()
      .then((settingsData) => {
        setSettings(settingsData);
        setComposerReady(true);
      })
      .catch((e: Error) => {
        setError(e.message);
        setComposerReady(true);
      });
    reloadPresets();
  }, []);

  useEffect(() => {
    if (!composerReady || flowOptions.length === 0) return;
    const preferred =
      (location.state as { flow_path?: string } | null)?.flow_path ||
      settings?.default_flow_path;
    setComposer((prev) => ({
      ...prev,
      flow_path:
        prev.flow_path && flowOptions.some((option) => option.path === prev.flow_path)
          ? prev.flow_path
          : resolveComposerFlowPath(preferred, flowOptions),
    }));
  }, [composerReady, flowOptions, settings?.default_flow_path, location.state]);

  const updateComposer = (patch: Partial<ComposerState>) => {
    setComposer((prev) => ({ ...prev, ...patch }));
  };

  const applyPreset = (preset: {
    name: string;
    slug?: string;
    topic_slug?: string;
    flow_path: string;
    default_model?: string;
    topics: string[];
  }) => {
    updateComposer({
      name: preset.name,
      flow_path: preset.flow_path,
      topic_slug: preset.topic_slug || "general",
      topics: [...preset.topics],
      presetSlug: preset.slug || "",
    });
    if (preset.default_model && settings) {
      setSettings({ ...settings, default_model: preset.default_model });
    }
  };

  const loadPresetFromServer = () => {
    if (!selectedPresetSlug) {
      setError("Choose a saved queue to load.");
      return;
    }
    setComposerBusy(true);
    setError(null);
    void api
      .getQueuePreset(selectedPresetSlug)
      .then(({ preset }) => {
        applyPreset(preset);
        setMessage(`Loaded saved queue “${preset.name}”.`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setComposerBusy(false));
  };

  const importPresetFile = (file: File | null) => {
    if (!file) return;
    setComposerBusy(true);
    setError(null);
    void readQueuePresetFile(file)
      .then((preset) =>
        api.saveQueuePreset({
          name: preset.name,
          slug: preset.slug,
          topic_slug: preset.topic_slug || "general",
          flow_path: preset.flow_path,
          default_model: preset.default_model || settings?.default_model || "",
          topics: preset.topics,
        }),
      )
      .then(({ preset }) => {
        applyPreset(preset);
        setMessage(`Imported and saved queue “${preset.name}”.`);
        reloadPresets();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setComposerBusy(false));
  };

  const savePreset = () => {
    const name = composer.name.trim();
    const flow_path = composer.flow_path.trim();
    const topics = composer.topics;
    if (!name) {
      setError("Enter a queue name before saving.");
      return;
    }
    if (!flow_path) {
      setError("Select a flow before saving.");
      return;
    }
    setComposerBusy(true);
    setError(null);
    void api
      .saveQueuePreset({
        name,
        slug: composer.presetSlug || undefined,
        topic_slug: composer.topic_slug.trim() || "general",
        flow_path,
        default_model: settings?.default_model || "",
        topics,
      })
      .then(({ preset }) => {
        updateComposer({ presetSlug: preset.slug });
        setMessage(`Saved queue “${preset.name}”.`);
        reloadPresets();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setComposerBusy(false));
  };

  const downloadPreset = () => {
    const name = composer.name.trim();
    const flow_path = composer.flow_path.trim();
    if (!name || !flow_path) {
      setError("Enter a queue name and flow before downloading.");
      return;
    }
    downloadQueuePresetFile({
      version: 1,
      name,
      slug: composer.presetSlug || undefined,
      topic_slug: composer.topic_slug.trim() || "general",
      flow_path,
      default_model: settings?.default_model || "",
      topics: composer.topics,
    });
    setMessage("Queue exported.");
  };

  const startQueue = (savePresetToo: boolean) => {
    if (!settings) return;
    const name = composer.name.trim();
    const flow_path = composer.flow_path.trim();
    const topics = composer.topics;
    if (!name) {
      setError("Enter a queue name.");
      return;
    }
    if (!flow_path) {
      setError("Select a flow.");
      return;
    }
    if (!settings.default_model) {
      setError("Select a model before starting.");
      return;
    }
    if (topics.length === 0) {
      setError("Add at least one topic to the list.");
      return;
    }
    setComposerBusy(true);
    setError(null);
    void api
      .startFlowQueue({
        name,
        flow_path,
        topic_slug: composer.topic_slug.trim() || "general",
        default_model: settings.default_model,
        topics,
        save_preset: savePresetToo,
        preset_slug: composer.presetSlug || undefined,
        enabled: true,
      })
      .then((result) => {
        const notice = result.message + (result.preset ? " Queue saved." : "");
        navigate("/queue?tab=running", { state: { message: notice } });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setComposerBusy(false));
  };

  const deletePreset = (slug: string, name: string) => {
    if (!window.confirm(`Delete saved queue “${name}”?`)) return;
    setComposerBusy(true);
    void api
      .deleteQueuePreset(slug)
      .then(() => {
        setMessage(`Deleted saved queue “${name}”.`);
        if (selectedPresetSlug === slug) setSelectedPresetSlug("");
        reloadPresets();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setComposerBusy(false));
  };

  const flowSelectOptions = ensureFlowSelectOption(
    flowOptions.length > 0 ? flowOptions : [{ path: DEFAULT_FLOW_PATH, label: DEFAULT_FLOW_PATH }],
    composer.flow_path,
  );

  return (
    <section className="card start-flows-page">
      <h2>Start flows</h2>
      <p className="hint">
        Name a queue, pick a flow and model, build a topic list (add individually or from a file),
        then start running. Saved queues are stored on the server for reuse. Track progress on{" "}
        <Link to="/queue?tab=running">Active</Link>.
      </p>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      <div className="start-flows-composer">
        <h3>Queue setup</h3>
        <div className="start-flows-composer-grid">
          <label>
            Queue name
            <input
              value={composer.name}
              onChange={(e) => updateComposer({ name: e.target.value })}
              placeholder="Sports articles"
            />
          </label>
          <label>
            Showroom topic
            <input
              value={composer.topic_slug}
              onChange={(e) => updateComposer({ topic_slug: e.target.value })}
              placeholder="sports"
            />
          </label>
          <label>
            Assigned flow
            <div className="flow-select-row">
              <select
                value={composer.flow_path}
                disabled={!composerReady || flowsLoading}
                onChange={(e) => updateComposer({ flow_path: e.target.value })}
              >
                {!composerReady || flowsLoading ? (
                  <option value="">Loading flows…</option>
                ) : null}
                {flowSelectOptions.map((option) => (
                  <option key={option.path} value={option.path}>
                    {option.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="secondary flow-select-refresh"
                disabled={flowsLoading}
                onClick={() => reloadFlowOptions()}
              >
                Refresh
              </button>
            </div>
          </label>
        </div>
        {settings?.default_flow_path && (
          <p className="hint">
            Factory default: {settings.default_flow_path}
            {composer.flow_path === settings.default_flow_path ? "" : " · "}
            {composer.flow_path !== settings.default_flow_path && (
              <>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => updateComposer({ flow_path: settings.default_flow_path })}
                >
                  Use default
                </button>
                {" · "}
              </>
            )}
            <Link to="/settings">Change default</Link>
          </p>
        )}

        {settings && (
          <div className="start-flows-model">
            <ModelSelectFields
              model={settings.default_model}
              onModelChange={(default_model) => setSettings({ ...settings, default_model })}
            />
          </div>
        )}

        <TopicListEditor
          topics={composer.topics}
          onChange={(topics) => updateComposer({ topics })}
          disabled={composerBusy}
        />

        <div className="start-flows-import-export">
          <details>
            <summary>Import / export backup</summary>
            <div className="start-flows-upload-row">
              <label className="file-upload-button secondary">
                Import .queue.json
                <input
                  ref={presetFileRef}
                  type="file"
                  accept=".json,.queue.json,application/json"
                  hidden
                  onChange={(e) => {
                    importPresetFile(e.target.files?.[0] ?? null);
                    e.target.value = "";
                  }}
                />
              </label>
              <button type="button" className="secondary" disabled={composerBusy} onClick={downloadPreset}>
                Export .queue.json
              </button>
              <span className="hint">Import saves to the server; export is for backup or moving environments.</span>
            </div>
          </details>
        </div>

        {presets.length > 0 && (
          <div className="start-flows-presets-row">
            <label>
              Saved queues
              <select value={selectedPresetSlug} onChange={(e) => setSelectedPresetSlug(e.target.value)}>
                <option value="">Choose a saved queue…</option>
                {presets.map((preset) => (
                  <option key={preset.slug} value={preset.slug}>
                    {preset.name} ({preset.topic_count} topics)
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="secondary" disabled={composerBusy} onClick={loadPresetFromServer}>
              Load saved queue
            </button>
          </div>
        )}

        <div className="flow-queue-actions start-flows-composer-actions">
          <button type="button" className="primary" disabled={composerBusy} onClick={() => startQueue(false)}>
            {composerBusy ? "Starting…" : "Start running"}
          </button>
          <button type="button" className="secondary" disabled={composerBusy} onClick={() => startQueue(true)}>
            Start &amp; save queue
          </button>
          <button type="button" className="secondary" disabled={composerBusy} onClick={savePreset}>
            Save queue
          </button>
        </div>
        <p className="hint">
          <Link to={`/flows/edit?path=${encodeURIComponent(composer.flow_path)}`}>Edit flow prompts</Link>
          {" · "}
          <Link to="/flows">Flow library</Link>
        </p>
      </div>

      {presets.length > 0 && (
        <div className="start-flows-saved-list">
          <h3>Saved queues</h3>
          <ul>
            {presets.map((preset) => (
              <li key={preset.slug}>
                <strong>{preset.name}</strong>
                <span className="hint">
                  {preset.flow_path} · {preset.topic_count} topics
                  {preset.default_model ? ` · ${preset.default_model}` : ""}
                </span>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setSelectedPresetSlug(preset.slug);
                    void api.getQueuePreset(preset.slug).then(({ preset: full }) => applyPreset(full));
                  }}
                >
                  Load
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => deletePreset(preset.slug, preset.name)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
