import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";
import { templateEditUrl } from "../utils/pipelineTemplates";

const STEP_COUNTS = [1, 2, 3, 4, 5, 6, 8, 10] as const;

export default function PipelineTemplateCreatePage() {
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("New pipeline template");
  const [slug, setSlug] = useState("new-template");
  const [folder, setFolder] = useState("");
  const [customSteps, setCustomSteps] = useState("2");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = (stepCount: number) => {
    setBusy(true);
    setError(null);
    void api
      .createPipelineTemplate({
        folder,
        slug: slug.trim() || "new-template",
        display_name: displayName.trim() || "New pipeline template",
        step_count: stepCount,
      })
      .then((result) => {
        notifyFlowsChanged();
        navigate(templateEditUrl(result.path));
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="card">
      <p>
        <Link to="/templates">← Pipeline templates</Link>
      </p>
      <h2>Create pipeline template</h2>
      <p className="hint">
        Templates define pipeline steps and prompts only — no beat coverage. After saving, apply the template to a desk
        from the desk page.
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
        <input
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          placeholder="Leave blank for flows root, or e.g. _templates"
        />
      </label>
      <p className="hint">
        Saved to the flows library (root or a folder). Existing files like <code>BetterWriterReviewer.flow.json</code>{" "}
        live at the root and appear in the template list automatically.
      </p>

      <h3>Starting step count</h3>
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
