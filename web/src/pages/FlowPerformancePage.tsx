import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import OutcomeCategoryChart from "../components/OutcomeCategoryChart";
import ImprovementReportPanel from "../components/ImprovementReportPanel";
import PromptImprovementDialog from "../components/PromptImprovementDialog";
import { TurnOutcomeChartPair } from "../components/TurnOutcomeBarChart";
import {
  completionRate,
  firstPassCompletedRate,
  firstPassYieldRate,
  num,
  pct,
} from "../utils/flowMetrics";
import {
  api,
  getApiKey,
  type FlowPerformanceData,
  type FlowVersionSummary,
  type PromptAnalysisResult,
  type PromptImprovementJob,
  type PromptImprovementReport,
  type PromptImprovementStep,
  type TopicQueueSnapshotSummary,
} from "../api";

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
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadMessage, setDownloadMessage] = useState<string | null>(null);
  const [apiKeyConfigured, setApiKeyConfigured] = useState<boolean | null>(null);
  const [improvementSteps, setImprovementSteps] = useState<PromptImprovementStep[]>([]);
  const [improvementJobs, setImprovementJobs] = useState<PromptImprovementJob[]>([]);
  const [allImprovementJobs, setAllImprovementJobs] = useState<PromptImprovementJob[]>([]);
  const [improvementDialog, setImprovementDialog] = useState<{
    scope: "step" | "flow";
    targetStepKey?: string;
    title: string;
    description: string;
  } | null>(null);
  const [improvementBusy, setImprovementBusy] = useState(false);
  const [completedNotice, setCompletedNotice] = useState<string | null>(null);
  const [activeReport, setActiveReport] = useState<PromptImprovementReport | null>(null);
  const [minCompletedRuns, setMinCompletedRuns] = useState(10);
  const handledJobCompletionsRef = useRef<Set<number>>(new Set());
  const reportPanelRef = useRef<HTMLElement | null>(null);

  const showReport = useCallback((report: PromptImprovementReport) => {
    setActiveReport(report);
  }, []);

  useEffect(() => {
    if (!activeReport) return;
    const timer = window.setTimeout(() => {
      reportPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [activeReport?.id]);

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

  useEffect(() => {
    if (!path) return;
    void api
      .listPromptImprovementJobs(path)
      .then(({ jobs }) => setAllImprovementJobs(jobs))
      .catch(() => setAllImprovementJobs([]));
  }, [path]);

  const reportIdByResultVersionId = useMemo(() => {
    const map = new Map<number, number>();
    for (const job of allImprovementJobs) {
      if (job.status === "completed" && job.result_flow_version_id && job.report_id) {
        map.set(job.result_flow_version_id, job.report_id);
      }
    }
    return map;
  }, [allImprovementJobs]);

  const openReportById = useCallback(
    (reportId: number) => {
      void api
        .getPromptImprovementReport(reportId)
        .then(({ report }) => showReport(report))
        .catch((e: Error) => setError(e.message));
    },
    [showReport],
  );

  const loadImprovementData = useCallback(async () => {
    if (!path || flowVersionId === "") {
      setImprovementSteps([]);
      setImprovementJobs([]);
      return;
    }
    const [stepsData, jobsData] = await Promise.all([
      api.getPromptImprovementSteps(path, flowVersionId),
      api.listPromptImprovementJobs(path, flowVersionId),
    ]);
    setImprovementSteps(stepsData.steps);
    setMinCompletedRuns(stepsData.min_completed_runs);
    setImprovementJobs(jobsData.jobs);
  }, [path, flowVersionId]);

  useEffect(() => {
    void loadImprovementData().catch((e: Error) => setError(e.message));
  }, [loadImprovementData]);

  useEffect(() => {
    const running = improvementJobs.filter((job) => job.status === "queued" || job.status === "running");
    if (running.length === 0) return;

    const handleCompletedJob = async (job: PromptImprovementJob) => {
      if (handledJobCompletionsRef.current.has(job.id)) return;
      handledJobCompletionsRef.current.add(job.id);
      const versionLabel = job.result_flow_version_id
        ? `v${versions.find((v) => v.id === job.result_flow_version_id)?.version_number ?? job.result_flow_version_id}`
        : "a new version";
      setCompletedNotice(`Prompt improvement finished — created ${versionLabel}.`);
      if (job.report_id) {
        try {
          const { report } = await api.getPromptImprovementReport(job.report_id);
          showReport(report);
        } catch (e) {
          setError(e instanceof Error ? e.message : "Could not load improvement report");
        }
      }
      await Promise.all([
        load(),
        loadImprovementData(),
        api.listPromptImprovementJobs(path).then(({ jobs }) => setAllImprovementJobs(jobs)),
      ]);
    };

    const timer = window.setInterval(() => {
      for (const job of running) {
        void api
          .getPromptImprovementJob(job.id)
          .then(async ({ job: latest }) => {
            setImprovementJobs((current) =>
              current.map((row) => (row.id === latest.id ? latest : row)),
            );
            if (latest.status === "completed") {
              await handleCompletedJob(latest);
            }
            if (latest.status === "failed") {
              handledJobCompletionsRef.current.add(latest.id);
              setError(latest.error_message || "Prompt improvement failed");
            }
          })
          .catch(() => undefined);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [improvementJobs, load, loadImprovementData, showReport, versions]);

  const completedRunCount = performance?.overall?.completed_count ?? 0;
  const canImprove = flowVersionId !== "" && completedRunCount >= minCompletedRuns;

  useEffect(() => {
    void api
      .authStatus()
      .then((status) => {
        setApiKeyConfigured(status.configured);
        if (status.configured && !getApiKey().trim()) {
          setError(
            'Factory API key is required. Open Settings and paste your key under "Use this key in this browser".',
          );
        }
      })
      .catch(() => {
        setApiKeyConfigured(null);
      });
  }, []);

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

  const downloadTelemetry = async () => {
    if (!path || flowVersionId === "") return;
    setDownloadBusy(true);
    setError(null);
    setDownloadMessage(null);
    try {
      await api.downloadTelemetryCsv(path, flowVersionId);
      setDownloadMessage("Telemetry CSV download started. Check your browser downloads folder.");
    } catch (e) {
      const message = e instanceof Error ? e.message : "Telemetry export failed";
      if (message.toLowerCase().includes("invalid api key")) {
        setError(
          'Invalid factory API key. Open Settings → Factory admin API key → paste the current key under "Use this key in this browser", then try again.',
        );
      } else if (message.toLowerCase().includes("no factory api key")) {
        setError(message);
      } else {
        setError(message);
      }
    } finally {
      setDownloadBusy(false);
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

  const startImprovement = async (model: string, puller: string) => {
    if (!path || flowVersionId === "" || !improvementDialog) return;
    setImprovementBusy(true);
    setError(null);
    setCompletedNotice(null);
    try {
      const { job } = await api.startPromptImprovement({
        path,
        flow_version_id: flowVersionId,
        scope: improvementDialog.scope,
        target_step_key: improvementDialog.targetStepKey || "",
        selected_model: model,
        selected_puller: puller,
      });
      setImprovementJobs((current) => [job, ...current.filter((row) => row.id !== job.id)]);
      setActiveReport(null);
    } finally {
      setImprovementBusy(false);
    }
  };

  if (!path) {
    return (
      <section className="panel">
        <h2>Prompt performance</h2>
        <p className="hint">Open this page from a flow in the library or editor.</p>
        <Link to="/flows">Desks</Link>
      </section>
    );
  }

  const overall = performance?.overall;

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
          <Link to="/flows">Desks</Link>
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

      <div className="flow-performance-telemetry-export">
        <button
          type="button"
          className="secondary"
          disabled={downloadBusy || flowVersionId === ""}
          onClick={() => void downloadTelemetry()}
        >
          {downloadBusy ? "Preparing…" : "Download Telemetry CSV"}
        </button>
        <p className="hint">
          Exports run, iteration, score, token, timing, and outcome telemetry for this flow version.
        </p>
        {apiKeyConfigured && !getApiKey().trim() && (
          <p className="error">
            Set your factory API key in <Link to="/settings">Settings</Link> before downloading.
          </p>
        )}
        {downloadMessage && <p className="hint">{downloadMessage}</p>}
      </div>

      {flowVersionId !== "" && (
        <section className="flow-prompt-improvement-panel">
          <h3>Prompt improvement</h3>
          <p className="hint">
            Uses telemetry, full iteration history, and top/bottom 25% example runs to create a new
            flow version with improved prompts. Requires at least {minCompletedRuns} completed runs
            ({completedRunCount} available).
          </p>
          {!canImprove && (
            <p className="hint">
              Select a version with enough completed runs to run new prompt improvements.
            </p>
          )}
          {canImprove && (
            <div className="flow-prompt-improvement-steps">
              {improvementSteps.map((step) => (
                <button
                  key={step.step_key}
                  type="button"
                  className="secondary"
                  onClick={() =>
                    setImprovementDialog({
                      scope: "step",
                      targetStepKey: step.step_key,
                      title: `Improve ${step.label}`,
                      description: `Analyze telemetry for ${step.label} (${step.step_key}) and create a new flow version with updated prompts.`,
                    })
                  }
                >
                  Improve {step.label}
                </button>
              ))}
              <button
                type="button"
                className="primary"
                onClick={() =>
                  setImprovementDialog({
                    scope: "flow",
                    title: "Improve entire flow",
                    description:
                      "Analyze all editable prompts in this version and create a new flow version with coordinated updates.",
                  })
                }
              >
                Improve entire flow
              </button>
            </div>
          )}

          {completedNotice && (
            <div className="flow-prompt-improvement-banner" role="status">
              {completedNotice}
            </div>
          )}

          {improvementJobs.length > 0 && (
            <div className="flow-prompt-improvement-jobs">
              <h4>Improvement jobs</h4>
              <p className="hint">
                Open a completed job&apos;s analysis to read patterns, conclusions, and why each prompt changed.
              </p>
              {improvementJobs.map((job) => (
                <div key={job.id} className="flow-prompt-improvement-job">
                  <strong>
                    {job.scope === "flow" ? "Entire flow" : job.target_step_key || "Step"} — {job.status}
                  </strong>
                  <p className="hint">
                    {job.progress_stage || "queued"} ({job.progress_percent}%)
                    {job.result_flow_version_id ? (
                      <>
                        {" · "}
                        <Link
                          to={`/flows/edit?path=${encodeURIComponent(path)}&version_id=${job.result_flow_version_id}`}
                        >
                          View new version
                        </Link>
                      </>
                    ) : null}
                  </p>
                  {job.report_id ? (
                    <button
                      type="button"
                      className={activeReport?.job_id === job.id ? "primary" : "secondary"}
                      onClick={() => openReportById(job.report_id!)}
                    >
                      {activeReport?.job_id === job.id ? "Showing analysis" : "View analysis"}
                    </button>
                  ) : null}
                  {job.error_message && <p className="error">{job.error_message}</p>}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {activeReport && (
        <ImprovementReportPanel report={activeReport} panelRef={reportPanelRef} />
      )}

      {overall && (
        <div className="flow-performance-summary">
          <div className="flow-performance-kpi flow-performance-kpi-primary">
            <span className="flow-performance-kpi-label">Artifact yield</span>
            <strong className="flow-performance-kpi-value">{pct(completionRate(overall))}</strong>
            <span className="hint">
              {overall.completed_count ?? 0} / {overall.run_count ?? 0} runs produced a final artifact
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">First-pass yield</span>
            <strong className="flow-performance-kpi-value">{pct(firstPassYieldRate(overall))}</strong>
            <span className="hint">
              {overall.first_pass_count ?? 0} / {overall.run_count ?? 0} all runs accepted on first review
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">First-pass (completed only)</span>
            <strong className="flow-performance-kpi-value">{pct(firstPassCompletedRate(overall))}</strong>
            <span className="hint">
              {overall.first_pass_count ?? 0} / {overall.completed_count ?? 0} completions without a rewrite loop
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Failures</span>
            <strong className="flow-performance-kpi-value">{overall.failure_count ?? 0}</strong>
            <span className="hint">of {overall.run_count ?? 0} dispatched</span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Review cycles</span>
            <strong className="flow-performance-kpi-value">{num(overall.median_review_rounds, 0)}</strong>
            <span className="hint">
              median among completed (avg {num(overall.avg_review_rounds, 0)})
            </span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Step turns</span>
            <strong className="flow-performance-kpi-value">{num(overall.median_step_turns, 0)}</strong>
            <span className="hint">median tool turns (avg {num(overall.avg_step_turns, 0)})</span>
          </div>
          <div className="flow-performance-kpi">
            <span className="flow-performance-kpi-label">Avg tokens</span>
            <strong className="flow-performance-kpi-value">
              {overall.avg_tokens ? Math.round(overall.avg_tokens).toLocaleString() : "—"}
            </strong>
            <span className="hint">per completed run</span>
          </div>
        </div>
      )}

      {performance && (performance.overall.error_groups?.length ?? 0) > 0 && (
        <OutcomeCategoryChart
          rows={performance.overall.error_groups ?? []}
          totalRuns={performance.overall.run_count}
        />
      )}

      {performance?.overall.turn_charts && (
        <TurnOutcomeChartPair charts={performance.overall.turn_charts} />
      )}

      {performance && performance.overall.run_count === 0 && (
        <section className="flow-performance-empty">
          <p>No runs recorded for this flow yet.</p>
          <p className="hint">
            Start a roster from <Link to="/start-flows">Plan a shift</Link>, then return here.
            Save a flow version after prompt changes to track performance by version.
          </p>
        </section>
      )}

      {performance && performance.overall.run_count > 0 && (performance.batches?.length ?? 0) === 0 && (
        <p className="hint flow-performance-empty-hint">
          Runs exist but no topic queue batches are linked yet. New dispatches attach batch snapshots automatically.
        </p>
      )}

      {performance && (performance.batches?.length ?? 0) > 0 && (
        <section className="flow-batch-list">
          <h3>Topic queue batches</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Runs</th>
                <th>Artifact yield</th>
                <th>First-pass yield</th>
                <th>First-pass (done)</th>
                <th>Failures</th>
                <th>Review cycles</th>
                <th>Step turns</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {performance.batches.map((batch) => (
                <tr key={batch.topic_queue_snapshot_id ?? batch.queue_name}>
                  <td>{batch.queue_name || `Queue #${batch.topic_queue_snapshot_id}`}</td>
                  <td>{batch.run_count}</td>
                  <td>{pct(completionRate(batch))}</td>
                  <td>{pct(firstPassYieldRate(batch))}</td>
                  <td>{pct(firstPassCompletedRate(batch))}</td>
                  <td>{batch.failure_count ?? 0}</td>
                  <td>
                    {num(batch.median_review_rounds, 0)}{" "}
                    <span className="hint">avg {num(batch.avg_review_rounds, 0)}</span>
                  </td>
                  <td>
                    {num(batch.median_step_turns, 0)}{" "}
                    <span className="hint">avg {num(batch.avg_step_turns, 0)}</span>
                  </td>
                  <td>
                    {batch.topic_queue_snapshot_id ? (
                      <Link
                        to={`/flows/batch?path=${encodeURIComponent(path)}&snapshot=${batch.topic_queue_snapshot_id}`}
                      >
                        Compare
                      </Link>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
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
                  <Link
                    to={`/flows/edit?path=${encodeURIComponent(path)}&version_id=${version.id}`}
                    className="secondary"
                  >
                    View prompts
                  </Link>
                  {reportIdByResultVersionId.has(version.id) ? (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => openReportById(reportIdByResultVersionId.get(version.id)!)}
                    >
                      Why prompts changed
                    </button>
                  ) : null}
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
                <th>Review cycles</th>
                <th>Step turns</th>
                <th>Outcome</th>
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
                  <td>{run.review_cycles ?? run.review_rounds ?? run.review_round ?? "—"}</td>
                  <td>{run.total_step_turns ?? "—"}</td>
                  <td>{run.error_group_label || "—"}</td>
                  <td>{run.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {improvementDialog && (
        <PromptImprovementDialog
          title={improvementDialog.title}
          description={improvementDialog.description}
          busy={improvementBusy}
          onClose={() => setImprovementDialog(null)}
          onSubmit={startImprovement}
        />
      )}
    </section>
  );
}
