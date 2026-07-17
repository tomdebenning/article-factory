import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type FlowTemplate } from "../api";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";
import { deskDetailUrl } from "../utils/desks";

const STEP_COUNTS = [1, 2, 3, 4, 5, 6, 8, 10] as const;

const FEATURED_BEAT_SLUGS = ["sports", "business-news", "tech-news", "ai-news"] as const;

const FEATURED_FALLBACK: Record<(typeof FEATURED_BEAT_SLUGS)[number], string> = {
  sports: "Games, athletes, leagues, and the stories fans care about.",
  "business-news": "Markets, companies, policy, and economic trends.",
  "tech-news": "Products, platforms, security, and industry moves.",
  "ai-news": "Models, tools, regulation, and real-world AI impact.",
};

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

  const featuredTemplates = useMemo(() => {
    const bySlug = new Map(templates.map((template) => [template.slug, template]));
    return FEATURED_BEAT_SLUGS.map((beatSlug) => bySlug.get(beatSlug)).filter(
      (template): template is FlowTemplate => template !== undefined,
    );
  }, [templates]);

  const otherTemplates = useMemo(
    () => templates.filter((template) => !FEATURED_BEAT_SLUGS.includes(template.slug as (typeof FEATURED_BEAT_SLUGS)[number])),
    [templates],
  );

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
        navigate(deskDetailUrl(result.path), {
          state: { flow_path: result.path },
        });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const createFromTemplate = (template: FlowTemplate) => {
    setBusy(true);
    setError(null);
    const beatSlug = template.slug;
    const beatFolder = folder.trim() || beatSlug;
    void api
      .createFlowFromTemplate({
        template_path: template.path,
        folder: beatFolder,
        slug: slug.trim() || beatSlug,
        display_name: displayName.trim() || template.display_name,
      })
      .then((result) => {
        notifyFlowsChanged();
        navigate(deskDetailUrl(result.path), {
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
        Start from a beat template or pick a blank step count. You can add, remove, and reorder steps in the editor.
        Choose the model when you plan a shift on <Link to="/start-flows">Plan a shift</Link>.
      </p>
      {error && <p className="error">{error}</p>}

      {featuredTemplates.length > 0 && (
        <>
          <h3>Beat desks</h3>
          <p className="hint">Four ready-made desks — same 4-step pipeline, tuned for each beat.</p>
          <div className="featured-beat-grid">
            {featuredTemplates.map((template) => (
              <article key={template.path} className="featured-beat-card">
                <h4>{template.display_name}</h4>
                <p className="hint">
                  {template.beat_brief ||
                    FEATURED_FALLBACK[template.slug as (typeof FEATURED_BEAT_SLUGS)[number]] ||
                    `${template.step_count} steps`}
                </p>
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => createFromTemplate(template)}
                >
                  Use {template.display_name}
                </button>
              </article>
            ))}
          </div>
        </>
      )}

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

      {otherTemplates.length > 0 && (
        <>
          <h3>Other templates</h3>
          <p className="hint">Templates live in <code>_templates/</code> and are copied into your chosen folder.</p>
          <ul className="flow-template-list">
            {otherTemplates.map((template) => (
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
