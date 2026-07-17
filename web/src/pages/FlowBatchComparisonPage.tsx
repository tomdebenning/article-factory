import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api,
  type BatchComparisonData,
  type ErrorGroupOption,
} from "../api";
import { firstPassCompletedRate, firstPassYieldRate, num, pct } from "../utils/flowMetrics";
import { TurnOutcomeChartPair } from "../components/TurnOutcomeBarChart";

export default function FlowBatchComparisonPage() {
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path") || "";
  const snapshotIdParam = searchParams.get("snapshot");
  const snapshotId = snapshotIdParam ? Number(snapshotIdParam) : null;

  const [data, setData] = useState<BatchComparisonData | null>(null);
  const [errorGroups, setErrorGroups] = useState<ErrorGroupOption[]>([]);
  const [flowVersionId, setFlowVersionId] = useState<number | "">("");
  const [selectedModel, setSelectedModel] = useState("");
  const [selectedPuller, setSelectedPuller] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busyRunId, setBusyRunId] = useState<string | null>(null);
  const [draftTags, setDraftTags] = useState<Record<string, { error_group: string; note: string }>>({});

  const models = useMemo(() => {
    if (!data) return [];
    const values = new Set<string>();
    for (const row of data.topics) {
      if (row.selected_model) values.add(row.selected_model);
    }
    return [...values].sort();
  }, [data]);

  const pullers = useMemo(() => {
    if (!data) return [];
    const values = new Set<string>();
    for (const row of data.topics) {
      if (row.selected_puller) values.add(row.selected_puller);
    }
    return [...values].sort();
  }, [data]);

  const load = useCallback(async () => {
    if (!snapshotId) return;
    setError(null);
    const [comparison, groups] = await Promise.all([
      api.getBatchComparison(snapshotId, {
        flow_version_id: flowVersionId === "" ? undefined : flowVersionId,
        selected_model: selectedModel || undefined,
        selected_puller: selectedPuller || undefined,
      }),
      api.listErrorGroups(),
    ]);
    setData(comparison);
    setErrorGroups(groups.error_groups);
    const drafts: Record<string, { error_group: string; note: string }> = {};
    for (const row of comparison.topics) {
      if (!row.run_id) continue;
      drafts[row.run_id] = {
        error_group: row.manual_tag || row.auto_error_group || row.error_group || "",
        note: row.manual_note || "",
      };
    }
    setDraftTags(drafts);
  }, [snapshotId, flowVersionId, selectedModel, selectedPuller]);

  useEffect(() => {
    void load().catch((e: Error) => setError(e.message));
  }, [load]);

  const saveTag = async (runId: string) => {
    const draft = draftTags[runId];
    if (!draft) return;
    setBusyRunId(runId);
    setError(null);
    try {
      await api.saveRunErrorTag(runId, {
        error_group: draft.error_group || undefined,
        note: draft.note,
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save tag");
    } finally {
      setBusyRunId(null);
    }
  };

  if (!snapshotId) {
    return (
      <section className="panel">
        <h2>Batch comparison</h2>
        <p className="hint">Open this page from Prompt performance with a topic queue batch selected.</p>
        <Link to={path ? `/flows/performance?path=${encodeURIComponent(path)}` : "/flows"}>
          Back to performance
        </Link>
      </section>
    );
  }

  const snapshot = data?.snapshot;
  const summary = data?.summary;

  return (
    <section className="panel flow-batch-page">
      <div className="flow-performance-head">
        <div>
          <h2>Batch comparison</h2>
          <p className="hint">
            {snapshot?.queue_name || snapshot?.queue_slug || `Queue #${snapshotId}`}
            {path ? ` · ${path}` : ""}
          </p>
        </div>
        <div className="flow-performance-actions">
          {path && (
            <Link to={`/flows/performance?path=${encodeURIComponent(path)}`} className="secondary">
              Prompt performance
            </Link>
          )}
          <Link to="/flows">Desks</Link>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="flow-performance-filters">
        <label>
          Flow version
          <input
            type="number"
            min={1}
            placeholder="All"
            value={flowVersionId}
            onChange={(e) => setFlowVersionId(e.target.value ? Number(e.target.value) : "")}
          />
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
        <label>
          Puller
          <select value={selectedPuller} onChange={(e) => setSelectedPuller(e.target.value)}>
            <option value="">All pullers</option>
            {pullers.map((puller) => (
              <option key={puller} value={puller}>
                {puller}
              </option>
            ))}
          </select>
        </label>
      </div>

      {summary && (
        <div className="flow-performance-summary">
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">First-pass yield</span>
            <strong className="flow-performance-kpi-value">{pct(firstPassYieldRate(summary))}</strong>
            <span className="hint">
              {summary.first_pass_count ?? 0} / {summary.run_count ?? 0} all runs
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">First-pass (completed only)</span>
            <strong className="flow-performance-kpi-value">{pct(firstPassCompletedRate(summary))}</strong>
            <span className="hint">
              {summary.first_pass_count ?? 0} / {summary.completed_count ?? 0} completions
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Completed</span>
            <strong className="flow-performance-kpi-value">
              {summary.completed_count ?? 0} / {summary.run_count ?? 0}
            </strong>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Failures</span>
            <strong className="flow-performance-kpi-value">{summary.failure_count ?? 0}</strong>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Review cycles</span>
            <strong className="flow-performance-kpi-value">{num(summary.median_review_rounds, 0)}</strong>
            <span className="hint">median among completed (avg {num(summary.avg_review_rounds, 0)})</span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Avg step turns</span>
            <strong className="flow-performance-kpi-value">{num(summary.avg_step_turns)}</strong>
            <span className="hint">median {num(summary.median_step_turns)}</span>
          </div>
        </div>
      )}

      {data && data.error_groups.length > 0 && (
        <section className="flow-batch-errors">
          <h3>Error groups</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Group</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {data.error_groups.map((row) => (
                <tr key={row.error_group}>
                  <td>{row.error_group_label}</td>
                  <td>{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data?.turn_charts && <TurnOutcomeChartPair charts={data.turn_charts} />}

      {data && (
        <section className="flow-batch-topics">
          <h3>Topics in batch ({data.topics.length})</h3>
          <table className="data-table flow-batch-table">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Run</th>
                <th>Status</th>
                <th>Error group</th>
                <th>Review cycles</th>
                <th>Step turns</th>
                <th>First pass</th>
                <th>Tag / note</th>
              </tr>
            </thead>
            <tbody>
              {data.topics.map((row) => {
                const runId = row.run_id || "";
                const draft = runId ? draftTags[runId] : undefined;
                return (
                  <tr key={row.queue_item_id ?? row.topic_slug}>
                    <td>
                      <div>{row.topic_slug || "—"}</div>
                      {row.prompt_preview && <div className="hint">{row.prompt_preview}</div>}
                    </td>
                    <td>
                      {row.run_id ? (
                        <Link to={`/runs/${row.run_id}`}>{row.run_id}</Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{row.status}</td>
                    <td>
                      <div>{row.error_group_label}</div>
                      {row.error_message && <div className="hint">{row.error_message}</div>}
                    </td>
                    <td>{row.review_cycles ?? row.review_rounds ?? "—"}</td>
                    <td>{row.total_step_turns ?? "—"}</td>
                    <td>
                      {row.first_pass_accept === true
                        ? "Yes"
                        : row.first_pass_accept === false
                          ? "No"
                          : "—"}
                    </td>
                    <td>
                      {row.run_id && draft ? (
                        <div className="flow-batch-tag-editor">
                          <select
                            value={draft.error_group}
                            onChange={(e) =>
                              setDraftTags((prev) => ({
                                ...prev,
                                [runId]: { ...draft, error_group: e.target.value },
                              }))
                            }
                          >
                            <option value="">Auto ({row.auto_error_group || row.error_group})</option>
                            {errorGroups.map((group) => (
                              <option key={group.error_group} value={group.error_group}>
                                {group.error_group_label}
                              </option>
                            ))}
                          </select>
                          <input
                            type="text"
                            placeholder="Note"
                            value={draft.note}
                            onChange={(e) =>
                              setDraftTags((prev) => ({
                                ...prev,
                                [runId]: { ...draft, note: e.target.value },
                              }))
                            }
                          />
                          <button
                            type="button"
                            className="secondary"
                            disabled={busyRunId === runId}
                            onClick={() => void saveTag(runId)}
                          >
                            Save
                          </button>
                        </div>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
    </section>
  );
}
