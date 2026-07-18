import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type FlowVersionSummary } from "../api";
import { templateEditUrl, type PipelineTemplateSummary } from "../utils/pipelineTemplates";

type Props = {
  templates: PipelineTemplateSummary[];
  selectedPath: string;
  selectedVersionId: number | "";
  applying: boolean;
  onSelectPath: (path: string) => void;
  onSelectVersion: (versionId: number | "") => void;
  onApply: () => void;
  showCreateLink?: boolean;
};

export default function PipelineTemplatePicker({
  templates,
  selectedPath,
  selectedVersionId,
  applying,
  onSelectPath,
  onSelectVersion,
  onApply,
  showCreateLink = true,
}: Props) {
  const [versions, setVersions] = useState<FlowVersionSummary[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const selectedTemplate = templates.find((template) => template.path === selectedPath);

  useEffect(() => {
    if (!selectedPath) {
      setVersions([]);
      return;
    }
    setVersionsLoading(true);
    void api
      .listFlowVersions(selectedPath)
      .then((data) => setVersions(data.versions))
      .catch(() => setVersions([]))
      .finally(() => setVersionsLoading(false));
  }, [selectedPath]);

  const handlePathChange = (nextPath: string) => {
    onSelectPath(nextPath);
    onSelectVersion("");
  };

  return (
    <div className="pipeline-template-picker">
      <label>
        Pipeline template
        <select value={selectedPath} onChange={(e) => handlePathChange(e.target.value)}>
          <option value="">Choose a template…</option>
          {templates.map((template) => (
            <option key={template.path} value={template.path}>
              {template.display_name} ({template.step_count} step{template.step_count === 1 ? "" : "s"})
              {template.version_count
                ? ` · ${template.version_count} saved version${template.version_count === 1 ? "" : "s"}`
                : ""}
            </option>
          ))}
        </select>
      </label>

      {selectedPath && (
        <label>
          Version
          <select
            value={selectedVersionId === "" ? "" : String(selectedVersionId)}
            disabled={versionsLoading}
            onChange={(e) => onSelectVersion(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">Current file on disk</option>
            {versions.map((version) => (
              <option key={version.id} value={version.id}>
                v{version.version_number}
                {version.message ? ` — ${version.message}` : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      {selectedTemplate && (
        <p className="hint">
          <code>{selectedTemplate.path}</code>
          {selectedTemplate.latest_version ? (
            <>
              {" · "}
              Latest saved: v{selectedTemplate.latest_version.version_number}
            </>
          ) : null}
          {" · "}
          <Link to={templateEditUrl(selectedTemplate.path)}>Edit template</Link>
          {" · "}
          <Link to={`/flows/performance?path=${encodeURIComponent(selectedTemplate.path)}`}>Versions</Link>
        </p>
      )}

      <div className="desk-page-actions">
        <button
          type="button"
          className="primary"
          disabled={!selectedPath || applying}
          onClick={onApply}
        >
          {applying ? "Applying…" : "Apply to desk"}
        </button>
        {showCreateLink && (
          <Link to="/templates/new" className="secondary">
            Create new template
          </Link>
        )}
        <Link to="/templates" className="secondary">
          Browse templates
        </Link>
      </div>
    </div>
  );
}
