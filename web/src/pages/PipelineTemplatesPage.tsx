import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type FlowVersionSummary } from "../api";
import { isPipelineTemplateSummary } from "../utils/desks";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";
import { templateEditUrl, type PipelineTemplateSummary } from "../utils/pipelineTemplates";

export default function PipelineTemplatesPage() {
  const [templates, setTemplates] = useState<PipelineTemplateSummary[]>([]);
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [versions, setVersions] = useState<FlowVersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyPath, setBusyPath] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    void api
      .listPipelineTemplates()
      .then((data) => {
        setTemplates(data.templates.filter(isPipelineTemplateSummary));
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
  }, []);

  const toggleVersions = (path: string) => {
    if (expandedPath === path) {
      setExpandedPath(null);
      setVersions([]);
      return;
    }
    setExpandedPath(path);
    setVersionsLoading(true);
    void api
      .listFlowVersions(path)
      .then((data) => setVersions(data.versions))
      .catch(() => setVersions([]))
      .finally(() => setVersionsLoading(false));
  };

  const deleteTemplate = (path: string, displayName: string) => {
    if (
      !window.confirm(
        `Delete template "${displayName}"?\n\nThis removes ${path} and cannot be undone. Desks that already applied this template keep their current pipeline.`,
      )
    ) {
      return;
    }
    setBusyPath(path);
    setError(null);
    setMessage(null);
    void api
      .deleteFlow(path)
      .then(() => {
        if (expandedPath === path) {
          setExpandedPath(null);
          setVersions([]);
        }
        setMessage(`Deleted ${path}`);
        reload();
        notifyFlowsChanged();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusyPath(null));
  };

  return (
    <section className="card">
      <div className="flows-page-head">
        <h2>Pipeline templates</h2>
        <div className="flow-page-actions">
          <Link to="/templates/new" className="primary">
            Create template
          </Link>
        </div>
      </div>
      <p className="hint">
        Reusable step-and-prompt pipelines stored under <code>_templates/</code> or as standalone flow files without
        desk coverage (beat brief or Edition topic). Desks apply a template for how work gets done — manage desks on
        the <Link to="/flows">Desks</Link> page.
      </p>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}
      {loading && <p className="hint">Loading templates…</p>}
      {!loading && templates.length === 0 && (
        <p className="hint">
          No pipeline templates yet. <Link to="/templates/new">Create one</Link>.
        </p>
      )}
      {templates.length > 0 && (
        <ul className="flow-template-list">
          {templates.map((template) => (
            <li key={template.path} className="flow-template-item">
              <div className="flow-template-main">
                <strong>{template.display_name}</strong>
                <span className="hint">{template.path}</span>
                <span className="hint">
                  {template.step_count} step{template.step_count === 1 ? "" : "s"}
                  {template.version_count
                    ? ` · ${template.version_count} saved version${template.version_count === 1 ? "" : "s"}`
                    : " · no saved versions yet"}
                </span>
              </div>
              <div className="flow-file-list-actions">
                <Link to={templateEditUrl(template.path)} className="secondary">
                  Edit prompts
                </Link>
                <Link to={`/flows/performance?path=${encodeURIComponent(template.path)}`} className="secondary">
                  Performance & versions
                </Link>
                <button type="button" className="secondary" onClick={() => toggleVersions(template.path)}>
                  {expandedPath === template.path ? "Hide versions" : "Show versions"}
                </button>
                <button
                  type="button"
                  className="secondary run-delete-button"
                  disabled={busyPath === template.path}
                  onClick={() => deleteTemplate(template.path, template.display_name)}
                >
                  {busyPath === template.path ? "Deleting…" : "Delete"}
                </button>
              </div>
              {expandedPath === template.path && (
                <div className="pipeline-template-versions">
                  {versionsLoading && <p className="hint">Loading versions…</p>}
                  {!versionsLoading && versions.length === 0 && (
                    <p className="hint">
                      No saved versions yet. Save one from{" "}
                      <Link to={`/flows/performance?path=${encodeURIComponent(template.path)}`}>Performance</Link> after
                      editing prompts.
                    </p>
                  )}
                  {!versionsLoading &&
                    versions.map((version) => (
                      <div key={version.id} className="pipeline-template-version-row">
                        <span>
                          v{version.version_number}
                          {version.message ? ` — ${version.message}` : ""}
                        </span>
                        <Link to={`/templates/edit?path=${encodeURIComponent(template.path)}&version_id=${version.id}`}>
                          View
                        </Link>
                      </div>
                    ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
