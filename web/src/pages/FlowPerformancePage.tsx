import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api,
  type FlowPerformanceData,
  type FlowVersionSummary,
  type PromptAnalysisResult,
  type TopicQueueSnapshotSummary,
} from "../api";

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export default function FlowPerformancePage() {
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path") || "";
  const [versions, setVersions] = useState<FlowVersionSummary[]>([]);
  const [topicQueues, setTopicQueues] = useState<TopicQueueSnapshotSummary[]>([]);
  const [performance, setPerformance] = useState<FlowPerformanceData | null>(null);
  const [analysis, setAnalysis] = useState<PromptAnalysisResult | null>(null);
  const [flowVersionId, setFlowVersionId] = useState<number | "">("");
  const [topicQueueSnapshotId, setTopicQueueSnapshotId] = useState<number | "">("");
  const [selectedModel, setSelectedModel] = useState("");
  const [versionMessage, setVersionMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const models = useMemo(() => {
    if (!performance) return [];
    return performance.by_model.map((row) => row.model).filter((model) => model && model !== "—");
  }, [performance]);

  const load = useCallback(async () => {
    if (!path) return;
    setError(null);
    const [versionData, queueData, perfData] = await Promise.all([
      api.listFlowVersions(path),
      api.listFlowTopicQueues(path),
      api.getFlowPerformance(path, {
        flow_version_id: flowVersionId === "" ? undefined : flowVersionId,
        topic_queue_snapshot_id: topicQueueSnapshotId === "" ? undefined : topicQueueSnapshotId,
        selected_model: selectedModel || undefined,
      }),
    ]);
    setVersions(versionData.versions);
    setTopicQueues(queueData.topic_queues);
    setPerformance(perfData);
  }, [path, flowVersionId, topicQueueSnapshotId, selectedModel]);

  useEffect(() => {
    void load().catch((e: Error) => setError(e.message));
  }, [load]);

  const saveVersion = async () => {
    if (!path) return;
    setBusy(true);
    setError(null);
    try {
      await api.createFlowVersion(path, versionMessage);
      setVersionMessage("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save version");
    } finally {
      setBusy(false);
    }
  };

  const analyze = async () => {
    if (!path) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.analyzeFlow({
        path,
        flow_version_id: flowVersionId === "" ? undefined : flowVersionId,
        topic_queue_snapshot_id: topicQueueSnapshotId === "" ? undefined : topicQueueSnapshotId,
        selected_model: selectedModel || undefined,
      });
      setAnalysis(result.analysis);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setBusy(false);
    }
  };

  if (!path) {
    return (
      <section className="panel">
        <h2>Prompt performance</h2>
        <p className="hint">Open this page from a flow in the library or editor.</p>
        <Link to="/flows">Flow library</Link>
      </section>
    );
  }

  return (
    <section className="panel flow-performance-page">
      <div className="flow-performance-head">
        <div>
          <h2>Prompt performance</h2>
          <p className="hint">{path}</p>
        </div>
        <div className="flow-performance-actions">
          <Link to={`/flows/edit?path=${encodeURIComponent(path)}`} className="secondary">
            Edit prompts
          </Link>
          <Link to="/flows">Flow library</Link>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="flow-performance-filters">
        <label>
          Flow version
          <select
            value={flowVersionId}
            onChange={(e) => setFlowVersionId(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">All versions</option>
            {versions.map((version) => (
              <option key={version.id} value={version.id}>
                v{version.version_number}
                {version.message ? ` — ${version.message}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Topic queue
          <select
            value={topicQueueSnapshotId}
            onChange={(e) => setTopicQueueSnapshotId(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">All topic queues</option>
            {topicQueues.map((queue) => (
              <option key={queue.id} value={queue.id}>
                {queue.queue_name || queue.queue_slug || `Queue #${queue.id}`} ({queue.topic_count} topics)
              </option>
            ))}
          </select>
        </label>
        <label>
          Model
          <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
            <option value="">All models</option>
            {models.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        </label>
      </div>

      {performance && (
        <div className="flow-performance-summary">
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">First-pass accept</span>
            <strong className="flow-performance-kpi-value">{pct(performance.overall.first_pass_rate)}</strong>
            <span className="hint">
              {performance.overall.first_pass_count ?? 0} / {performance.overall.completed_count ?? 0} completed runs
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Runs in cohort</span>
            <strong className="flow-performance-kpi-value">{performance.overall.run_count ?? 0}</strong>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Avg tokens</span>
            <strong className="flow-performance-kpi-value">
              {performance.overall.avg_tokens ? Math.round(performance.overall.avg_tokens).toLocaleString() : "—"}
            </strong>
          </div>
        </div>
      )}

      <div className="flow-performance-toolbar">
        <div className="flow-version-save">
          <input
            type="text"
            placeholder="Version note (optional)"
            value={versionMessage}
            onChange={(e) => setVersionMessage(e.target.value)}
          />
          <button type="button" className="secondary" disabled={busy} onClick={() => void saveVersion()}>
            Save flow version
          </button>
        </div>
        <button type="button" className="primary" disabled={busy} onClick={() => void analyze()}>
          Analyze flow
        </button>
      </div>

      {analysis && (
        <section className="flow-analysis-panel">
          <h3>Latest analysis</h3>
          <p>{analysis.summary}</p>
          {(analysis.suggestions || []).map((suggestion, index) => (
            <article key={`${suggestion.step_key}-${index}`} className="flow-analysis-suggestion">
              {suggestion.step_key && <h4>{suggestion.step_key}</h4>}
              <p>{suggestion.diagnosis}</p>
              <p>
                <strong>Suggestion:</strong> {suggestion.suggestion}
              </p>
              {suggestion.evidence?.length ? (
                <ul>
                  {suggestion.evidence.map((item, evidenceIndex) => (
                    <li key={evidenceIndex}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </section>
      )}

      <section className="flow-version-history">
        <h3>Version history</h3>
        {versions.length === 0 ? (
          <p className="hint">No saved versions yet. Save a version after prompt changes to start tracking performance.</p>
        ) : (
          <ul className="flow-version-list">
            {versions.map((version) => (
              <li key={version.id} className="flow-version-item">
                <div className="flow-version-item-head">
                  <strong>v{version.version_number}</strong>
                  <span className="hint">{version.created_at ? new Date(version.created_at).toLocaleString() : ""}</span>
                </div>
                {version.message && <p>{version.message}</p>}
                {version.changes_from_previous?.length ? (
                  <ul className="flow-version-changes">
                    {version.changes_from_previous.map((change, index) => (
                      <li key={index}>
                        {change.change} {change.step_key}
                        {change.field ? `.${change.field}` : ""}
                        {change.label ? ` (${change.label})` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="hint">Initial version</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {performance && performance.runs.length > 0 && (
        <section className="flow-performance-runs">
          <h3>Recent runs</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Topic</th>
                <th>Model</th>
                <th>First pass</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {performance.runs.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                  </td>
                  <td>{run.topic_slug}</td>
                  <td>{run.selected_model || "—"}</td>
                  <td>{run.first_pass_accept === true ? "Yes" : run.first_pass_accept === false ? "No" : "—"}</td>
                  <td>{run.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </section>
  );
}
