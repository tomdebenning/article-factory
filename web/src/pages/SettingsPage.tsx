import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, getApiKey, setApiKey, type FactorySettings } from "../api";
import ApiKeyCard from "../components/ApiKeyCard";
import ConnectionTestFeedback, {
  type ConnectionTestState,
} from "../components/ConnectionTestFeedback";
import ModelSelectFields from "../components/ModelSelectFields";
import SecretInput from "../components/SecretInput";
import { useFactoryIdentity } from "../context/FactoryIdentityContext";
import { useFlowSelectOptions } from "../hooks/useFlowSelectOptions";
import { ensureFlowSelectOption } from "../utils/flowSelectOptions";

const idleTest: ConnectionTestState = { status: "idle", message: "" };

export default function SettingsPage() {
  const { refreshFactoryIdentity } = useFactoryIdentity();
  const [settings, setSettings] = useState<FactorySettings | null>(null);
  const [factoryApiKey, setFactoryApiKey] = useState(getApiKey());
  const [authConfigured, setAuthConfigured] = useState(false);
  const [authMasked, setAuthMasked] = useState<string | null>(null);
  const [generatedFactoryKey, setGeneratedFactoryKey] = useState<string | null>(null);
  const [generatingFactoryKey, setGeneratingFactoryKey] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [cpTest, setCpTest] = useState<ConnectionTestState>(idleTest);
  const [cmsTest, setCmsTest] = useState<ConnectionTestState>(idleTest);
  const [braveTest, setBraveTest] = useState<ConnectionTestState>(idleTest);
  const { options: flowOptions, loading: flowsLoading, reload: reloadFlowOptions } = useFlowSelectOptions();
  const [factoryDisplayName, setFactoryDisplayName] = useState("");
  const [savingFactoryName, setSavingFactoryName] = useState(false);
  const [factoryNameMessage, setFactoryNameMessage] = useState<string | null>(null);
  const [factoryNameError, setFactoryNameError] = useState<string | null>(null);

  const loadAuthStatus = () => {
    void api
      .authStatus()
      .then((status) => {
        setAuthConfigured(status.configured);
        setAuthMasked(status.masked);
      })
      .catch(() => {
        /* auth status is optional during bootstrap */
      });
  };

  useEffect(() => {
    loadAuthStatus();
    void api
      .getSettings()
      .then((data) => {
        setSettings({
          ...data,
          brave_search_api_key: data.brave_search_api_key ?? "",
          brave_search_configured: data.brave_search_configured ?? false,
          gateway_id: data.gateway_id ?? "",
          gateway_display_name: data.gateway_display_name ?? "The Newsroom",
          display_timezone: data.display_timezone ?? "UTC",
          auto_scheduler_enabled: data.auto_scheduler_enabled ?? true,
        });
        setFactoryDisplayName(data.gateway_display_name ?? "The Newsroom");
      })
      .catch((e: Error) => setLoadError(e.message));
  }, []);

  const generateFactoryKey = async () => {
    setGeneratingFactoryKey(true);
    setGeneratedFactoryKey(null);
    try {
      const result = await api.generateAuthKey();
      setGeneratedFactoryKey(result.api_key);
      setFactoryApiKey(result.api_key);
      setApiKey(result.api_key);
      setAuthConfigured(true);
      setAuthMasked(`${result.api_key.slice(0, 4)}…${result.api_key.slice(-4)}`);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Could not generate factory API key.");
    } finally {
      setGeneratingFactoryKey(false);
    }
  };

  const saveFactoryName = () => {
    const name = factoryDisplayName.trim();
    if (!name) {
      setFactoryNameError("Enter a factory display name.");
      return;
    }
    setSavingFactoryName(true);
    setFactoryNameMessage(null);
    setFactoryNameError(null);
    void api
      .updateFactoryIdentity(name)
      .then((saved) => {
        setSettings(saved);
        setFactoryDisplayName(saved.gateway_display_name);
        refreshFactoryIdentity();
        setFactoryNameMessage("Factory name saved. It will appear on the control plane shortly.");
      })
      .catch((e: Error) => setFactoryNameError(e.message))
      .finally(() => setSavingFactoryName(false));
  };

  const save = () => {
    if (!settings) return;
    setApiKey(factoryApiKey);
    setSaveMessage(null);
    setSaveError(null);
    void api
      .saveSettings(settings)
      .then((saved) => {
        setSettings(saved);
        setSaveMessage("Settings saved.");
      })
      .catch((e: Error) => setSaveError(e.message));
  };

  const testConnection = (target: "control-plane" | "cms" | "brave-search") => {
    if (!settings) return;

    const setTest =
      target === "control-plane" ? setCpTest : target === "cms" ? setCmsTest : setBraveTest;
    setTest({ status: "testing", message: "" });

    if (target === "control-plane" && !settings.control_plane_url.trim()) {
      setTest({
        status: "error",
        message: "Enter a control plane URL before testing.",
      });
      return;
    }

    if (target === "cms") {
      if (!settings.cms_url.trim()) {
        setTest({
          status: "error",
          message: "Enter The Edition URL before testing.",
        });
        return;
      }
      if (!settings.cms_api_key.trim()) {
        setTest({
          status: "error",
          message: "Paste the newsroom integration key from The Edition admin (/admin) before testing.",
        });
        return;
      }
    }

    if (target === "brave-search" && !settings.brave_search_api_key.trim()) {
      setTest({
        status: "error",
        message: "Enter a Brave Search API key before testing.",
      });
      return;
    }

    const call =
      target === "control-plane"
        ? api.testControlPlane(settings)
        : target === "cms"
          ? api.testCms(settings)
          : api.testBraveSearch(settings);
    void call
      .then((result) => {
        if (result.ok) {
          setTest({
            status: "success",
            message: result.message || "Connection successful.",
          });
          return;
        }
        setTest({
          status: "error",
          message: result.message || "The connection test failed.",
        });
      })
      .catch((e: Error) => {
        setTest({
          status: "error",
          message: e.message || "Could not reach the factory API to run the test.",
        });
      });
  };

  if (!settings) {
    return loadError ? <p className="error">{loadError}</p> : <p>Loading…</p>;
  }

  return (
    <section className="card">
      <h2>Integration settings</h2>
      <p className="hint">
        Generate keys in each app, copy them across, then test the connection. Accepted artifacts
        publish to The Edition automatically.
      </p>

      <div className="step-card">
        <h3>Factory identity</h3>
        <p className="hint">
          Unique factory ID (assigned once) and a friendly name shown on the control plane Gateways list.
        </p>
        <label>
          Factory ID
          <input value={settings.gateway_id} readOnly />
        </label>
        <label>
          Display name
          <input
            value={factoryDisplayName}
            onChange={(e) => {
              setFactoryDisplayName(e.target.value);
              setFactoryNameMessage(null);
              setFactoryNameError(null);
            }}
            placeholder="e.g. Office Newsroom"
          />
        </label>
        {factoryNameMessage && <p className="ok">{factoryNameMessage}</p>}
        {factoryNameError && <p className="error">{factoryNameError}</p>}
        <button
          type="button"
          className="secondary"
          disabled={savingFactoryName || !factoryDisplayName.trim()}
          onClick={saveFactoryName}
        >
          {savingFactoryName ? "Saving…" : "Save factory name"}
        </button>
      </div>

      <div className="step-card">
        <h3>Factory admin API key</h3>
        <ApiKeyCard
          title="Key for this admin UI"
          description="Generate here, then keep it in this browser to access newsroom settings and the queue."
          configured={authConfigured}
          masked={authMasked}
          generatedKey={generatedFactoryKey}
          generating={generatingFactoryKey}
          onGenerate={generateFactoryKey}
          browserKey={factoryApiKey}
          onBrowserKeyChange={(value) => {
            setFactoryApiKey(value);
            setApiKey(value);
          }}
          browserKeyLabel="Use this key in this browser"
          browserKeyHint="Auto-filled when you generate a key. Paste here if you generated on another device."
        />
      </div>

      <div className="step-card step-card-tools">
        <h3>Tools — Brave Search</h3>
        <p className="hint">
          Required for the <strong>Web search (Brave)</strong> tool on desk steps. Get a key from{" "}
          <a href="https://brave.com/search/api/" target="_blank" rel="noreferrer">
            Brave Search API
          </a>
          , paste it here, then save settings.
        </p>
        <p className={`hint ${settings.brave_search_configured ? "ok" : "warn"}`}>
          {settings.brave_search_configured
            ? "Brave Search is configured."
            : "Not configured yet — the dashboard will remind you until this is set."}
        </p>
        <label>
          Brave Search API key
          <SecretInput
            value={settings.brave_search_api_key}
            onChange={(brave_search_api_key) => {
              setSettings({ ...settings, brave_search_api_key });
              setBraveTest(idleTest);
            }}
            autoComplete="off"
            placeholder="BSA…"
          />
        </label>
        <button
          type="button"
          className="secondary"
          disabled={braveTest.status === "testing"}
          onClick={() => testConnection("brave-search")}
        >
          {braveTest.status === "testing" ? "Testing…" : "Test Brave Search"}
        </button>
        <ConnectionTestFeedback
          state={braveTest}
          successTitle="Brave Search connected"
          errorTitle="Brave Search connection failed"
        />
      </div>

      <div className="step-card">
        <h3>Shift scheduler</h3>
        <p className="hint">
          Shift boundaries always run in UTC. The display timezone converts labels in the Newsroom UI only.
        </p>
        <label>
          Display timezone
          <select
            value={settings.display_timezone}
            onChange={(e) => setSettings({ ...settings, display_timezone: e.target.value })}
          >
            <option value="UTC">UTC</option>
            <option value="America/New_York">America/New_York</option>
            <option value="America/Chicago">America/Chicago</option>
            <option value="America/Los_Angeles">America/Los_Angeles</option>
            <option value="Europe/London">Europe/London</option>
          </select>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={settings.auto_scheduler_enabled}
            onChange={(e) => setSettings({ ...settings, auto_scheduler_enabled: e.target.checked })}
          />
          Auto-activate and complete shifts at UTC window boundaries
        </label>
      </div>

      <div className="step-card">
        <h3>Control plane</h3>
        <label>
          Control plane URL
          <input
            value={settings.control_plane_url}
            onChange={(e) => {
              setSettings({ ...settings, control_plane_url: e.target.value });
              setCpTest(idleTest);
            }}
            placeholder="http://sg02:8000"
          />
        </label>
        <p className="hint">
          Use the hostname where the control plane API listens (not 127.0.0.1 unless it runs on this
          machine).
        </p>
        <ModelSelectFields
          model={settings.default_model}
          onModelChange={(default_model) => setSettings({ ...settings, default_model })}
          label="Default model"
        />
        <button
          type="button"
          className="secondary"
          disabled={cpTest.status === "testing"}
          onClick={() => testConnection("control-plane")}
        >
          {cpTest.status === "testing" ? "Testing…" : "Test control plane"}
        </button>
        <ConnectionTestFeedback
          state={cpTest}
          successTitle="Control plane connected"
          errorTitle="Control plane connection failed"
        />
      </div>

      <div className="step-card">
        <h3>The Edition</h3>
        <p className="hint">
          In The Edition, open <a href="http://127.0.0.1:8200/admin" target="_blank" rel="noreferrer">/admin</a>,
          generate the newsroom integration key, copy it, and paste it below.
        </p>
        <label>
          The Edition URL
          <input
            value={settings.cms_url}
            onChange={(e) => {
              setSettings({ ...settings, cms_url: e.target.value });
              setCmsTest(idleTest);
            }}
            placeholder="http://127.0.0.1:8200"
          />
        </label>
        <label>
          Edition integration key (from /admin)
          <SecretInput
            value={settings.cms_api_key}
            onChange={(cms_api_key) => {
              setSettings({ ...settings, cms_api_key });
              setCmsTest(idleTest);
            }}
            autoComplete="off"
          />
        </label>
        <button
          type="button"
          className="secondary"
          disabled={cmsTest.status === "testing"}
          onClick={() => testConnection("cms")}
        >
          {cmsTest.status === "testing" ? "Testing…" : "Test The Edition"}
        </button>
        <ConnectionTestFeedback
          state={cmsTest}
          successTitle="The Edition connected"
          errorTitle="The Edition connection failed"
        />
      </div>

      <div className="step-card">
        <h3>Default desk</h3>
        <p className="hint">
          Used when roster items do not specify a desk. Desks are independent of Edition topic categories.
        </p>
        <label>
          Default desk path
          <div className="flow-select-row">
            <select
              value={settings.default_flow_path}
              disabled={flowsLoading}
              onChange={(e) => setSettings({ ...settings, default_flow_path: e.target.value })}
            >
              {ensureFlowSelectOption(
                flowOptions.length > 0
                  ? flowOptions
                  : [{ path: settings.default_flow_path, label: settings.default_flow_path }],
                settings.default_flow_path,
              ).map((option) => (
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
        <p className="hint">
          <Link to={`/flows/edit?path=${encodeURIComponent(settings.default_flow_path)}`}>
            Edit default desk prompts
          </Link>
        </p>
      </div>

      {(saveMessage || saveError) && (
        <p className={saveError ? "error save-feedback" : "ok save-feedback"} role="status">
          {saveError ?? saveMessage}
        </p>
      )}

      <button type="button" className="primary" onClick={save}>
        Save settings
      </button>
    </section>
  );
}
