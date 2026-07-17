import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type FlowTemplate } from "../api";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";

const STEP_COUNTS = [1, 2, 3, 4, 5, 6, 8, 10] as const;

export default function FlowCreatePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [displayName, setDisplayName] = useState("New desk");
  const [slug, setSlug] = useState("new-desk");
  const [folder, setFolder] = useState(searchParams.get("folder") || "");
  const [customSteps, setCustomSteps] = useState("4");
  const [templates, setTemplates] = useState<FlowTemplate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .listFlowTemplates()
      .then((data) => setTemplates(data.templates))
      .catch(() => {
        /* templates optional */
      });
  }, []);

  const create = (stepCount: number) => {
    setBusy(true);
    setError(null);
    void api
      .createFlow({
        folder,
        slug: slug.trim() || "new-desk",
        display_name: displayName.trim() || "New desk",
        step_count: stepCount,
      })
      .then((result) => {
        notifyFlowsChanged();
        navigate(`/flows/edit?path=${encodeURIComponent(result.path)}`, {
          state: { flow_path: result.path },
        });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const createFromTemplate = (template: FlowTemplate) => {
    setBusy(true);
    setError(null);
    void api
      .createFlowFromTemplate({
        template_path: template.path,
        folder,
        slug: slug.trim() || `${template.slug}-copy`,
        display_name: displayName.trim() || template.display_name,
      })
      .then((result) => {
        notifyFlowsChanged();
        navigate(`/flows/edit?path=${encodeURIComponent(result.path)}`, {
          state: { flow_path: result.path },
        });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="card">
      <p><Link to="/flows">← All desks</Link></p>
      <h2>Create desk</h2>
      <p className="hint">
        Start from a template or pick a blank step count. You can add, remove, and reorder steps in the editor.
        Choose the model when you plan a shift on <Link to="/start-flows">Plan a shift</Link>.
      </p>
      {error && <p className="error">{error}</p>}

      <label>
        Display name
        <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>
      <label>
        File slug
        <input value={slug} onChange={(e) => setSlug(e.target.value)} />
      </label>
      <label>
        Folder (optional)
        <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="sports" />
      </label>

      {templates.length > 0 && (
        <>
          <h3>Start from template</h3>
          <p className="hint">Templates live in <code>_templates/</code> and are copied into your chosen folder.</p>
          <ul className="flow-template-list">
            {templates.map((template) => (
              <li key={template.path} className="flow-template-item">
                <div className="flow-template-main">
                  <strong>{template.display_name}</strong>
                  <span className="hint">{template.path}</span>
                  <span className="hint">{template.step_count} step{template.step_count === 1 ? "" : "s"}</span>
                </div>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => createFromTemplate(template)}
                >
                  Use template
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Blank desk</h3>
      <div className="flow-create-buttons">
        {STEP_COUNTS.map((count) => (
          <button key={count} type="button" className="primary" disabled={busy} onClick={() => create(count)}>
            {count} step{count === 1 ? "" : "s"}
          </button>
        ))}
      </div>
      <label>
        Or custom step count (1–20)
        <input
          type="number"
          min={1}
          max={20}
          value={customSteps}
          onChange={(e) => setCustomSteps(e.target.value)}
        />
      </label>
      <button
        type="button"
        className="secondary"
        disabled={busy}
        onClick={() => {
          const count = Number(customSteps);
          if (count >= 1 && count <= 20) create(count);
        }}
      >
        Create with custom steps
      </button>
    </section>
  );
}
