import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import RunProgressPanel from "../components/RunProgressPanel";
import { api, type RunDetail } from "../api";

export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [rerunStatus, setRerunStatus] = useState<{ can_retry: boolean; message: string } | null>(null);

  const reload = () => {
    if (!runId) return;
    void api
      .getRun(runId)
      .then((data) => {
        setDetail(data);
        const queueItemId = data.run.queue_item_id;
        const rerunnable =
          data.run.status !== "running" &&
          queueItemId != null &&
          (data.run.status === "completed" ||
            data.run.status === "failed" ||
            data.run.status === "cancelled");
        if (rerunnable && queueItemId != null) {
          void api
            .getQueueRetryStatus(queueItemId)
            .then((status) => {
              setRerunStatus({ can_retry: status.can_retry, message: status.message });
            })
            .catch(() => setRerunStatus(null));
        } else {
          setRerunStatus(null);
        }
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    reload();
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [runId]);

  const stopRun = () => {
    if (!runId) return;
    setBusy(true);
    setError(null);
    void api
      .stopRun(runId)
      .then((result) => {
        setMessage(result.message);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const deleteRun = () => {
    if (!runId || !window.confirm(`Delete run ${runId}? This cannot be undone.`)) return;
    setBusy(true);
    setError(null);
    void api
      .deleteRun(runId)
      .then(() => navigate("/queue"))
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const publishToShowroom = () => {
    if (!runId) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    void api
      .publishRun(runId)
      .then(() => {
        setMessage("Published to Showroom.");
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const rerunPrompt = () => {
    const queueItemId = detail?.run.queue_item_id;
    if (queueItemId == null) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    void api
      .retryQueueItem(queueItemId)
      .then((result) => {
        if (result.ok) {
          setMessage(result.message);
          reload();
        } else {
          setError(result.message);
        }
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  if (error && !detail) return <p className="error">{error}</p>;
  if (!detail) return <p>Loading run…</p>;

  const isRunning = detail.run.status === "running";
  const canRerun =
    !isRunning &&
    detail.run.queue_item_id != null &&
    (detail.run.status === "completed" ||
      detail.run.status === "failed" ||
      detail.run.status === "cancelled");

  return (
    <section className="card">
      <div className="run-detail-head">
        <h2>Run {detail.run.run_id}</h2>
        <div className="run-detail-actions">
          {isRunning && (
            <button type="button" className="secondary" disabled={busy} onClick={stopRun}>
              {busy ? "Stopping…" : "Stop run"}
            </button>
          )}
          {!isRunning && (
            <>
              {canRerun && (
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || rerunStatus?.can_retry === false}
                  title={rerunStatus?.can_retry === false ? rerunStatus.message : undefined}
                  onClick={rerunPrompt}
                >
                  {busy ? "Re-running…" : "Re-run prompt"}
                </button>
              )}
              <button type="button" className="primary" disabled={busy} onClick={publishToShowroom}>
                {busy ? "Publishing…" : "Publish to Showroom"}
              </button>
              <button type="button" className="secondary run-delete-button" disabled={busy} onClick={deleteRun}>
                {busy ? "Deleting…" : "Delete run"}
              </button>
            </>
          )}
          <Link to="/queue" className="secondary">
            Back to queue
          </Link>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}
      {detail.run.status === "running" && (
        <p className="hint">
          This run will auto-publish to Showroom when it completes successfully.
        </p>
      )}
      {detail.run.status === "completed" && !detail.run.error && (
        <p className="ok">Auto-published to Showroom on completion.</p>
      )}
      {detail.run.status === "completed" && detail.run.error?.includes("Showroom") && (
        <p className="error">
          Auto-publish to Showroom failed. Use <strong>Publish to Showroom</strong> to retry.
        </p>
      )}
      <dl className="stats-dl">
        <div><dt>Status</dt><dd>{detail.run.status}</dd></div>
        <div><dt>Topic</dt><dd>{detail.run.topic_slug}</dd></div>
        <div>
          <dt>Flow</dt>
          <dd>
            {detail.run.flow_path ? (
              <Link to={`/flows/edit?path=${encodeURIComponent(detail.run.flow_path)}`}>
                {detail.run.flow_path}
              </Link>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div><dt>Current step</dt><dd>{detail.run.current_step ?? "—"}</dd></div>
        <div><dt>Review round</dt><dd>{detail.run.review_round}</dd></div>
        <div><dt>Model</dt><dd>{detail.run.selected_model || "—"}</dd></div>
        <div><dt>Puller</dt><dd>{detail.run.selected_puller || "—"}</dd></div>
        {detail.run.status === "completed" && (
          <div>
            <dt>First-pass accept</dt>
            <dd>
              {detail.run.first_pass_accept === true
                ? "Yes"
                : detail.run.first_pass_accept === false
                  ? "No"
                  : "—"}
            </dd>
          </div>
        )}
        {detail.run.flow_version_id != null && (
          <div>
            <dt>Flow version</dt>
            <dd>
              {detail.run.flow_path ? (
                <Link
                  to={`/flows/performance?path=${encodeURIComponent(detail.run.flow_path)}`}
                >
                  v{detail.run.flow_version_number ?? detail.run.flow_version_id}
                  {detail.run.flow_version_message ? ` — ${detail.run.flow_version_message}` : ""}
                </Link>
              ) : (
                `v${detail.run.flow_version_number ?? detail.run.flow_version_id}`
              )}
            </dd>
          </div>
        )}
        {detail.run.topic_queue_snapshot_id != null && (
          <div>
            <dt>Topic queue</dt>
            <dd>{detail.run.topic_queue_label ?? `Snapshot #${detail.run.topic_queue_snapshot_id}`}</dd>
          </div>
        )}
      </dl>
      {detail.run.error && <p className="error">{detail.run.error}</p>}
      {(detail.step_files?.length ?? 0) > 0 && (
        <div className="run-step-files">
          <h3>Saved step outputs</h3>
          <p className="hint">Markdown files written when a step had “Save response to disk” enabled.</p>
          <ul className="run-step-file-list">
            {detail.step_files!.map((file) => (
              <li key={file.name}>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setOpenFile(file.name);
                    void api
                      .getRunStepFile(runId, file.name)
                      .then((result) => setFileContent(result.content))
                      .catch((e: Error) => setError(e.message));
                  }}
                >
                  {file.name}
                </button>
                <span className="hint">{file.size_bytes} bytes</span>
              </li>
            ))}
          </ul>
          {openFile && fileContent !== null && (
            <div className="run-step-file-preview">
              <div className="run-step-file-preview-head">
                <strong>{openFile}</strong>
                <button type="button" className="secondary" onClick={() => setOpenFile(null)}>
                  Close
                </button>
              </div>
              <pre>{fileContent}</pre>
            </div>
          )}
        </div>
      )}
      <RunProgressPanel
        title={detail.run.topic_slug}
        status={detail.run.status}
        steps={detail.steps}
        flowSteps={detail.run.flow_steps}
        currentStep={detail.run.current_step}
        defaultOpen
        meta={
          <span className="hint">
            Model: {detail.run.selected_model || "—"} · Puller: {detail.run.selected_puller || "—"}
          </span>
        }
      />
    </section>
  );
}
