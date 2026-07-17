import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import RunProgressPanel from "../components/RunProgressPanel";
import { api, type CompletedArticle, type StepExecution, type ToolUseEntry } from "../api";
import { formatTokenCount } from "../utils/tokenFormat";

function manifestSteps(manifest: CompletedArticle["manifest"]): StepExecution[] {
  const raw = manifest?.step_stats || manifest?.steps;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.map((step, index) => ({
    id: index + 1,
    run_id: "",
    step_key: String(step.step_key || "step"),
    status: step.error ? "failed" : "completed",
    agent_id: String(step.agent_id || ""),
    conversation_id: String(step.conversation_id || ""),
    puller: String(step.puller || ""),
    model: String(step.model || ""),
    duration_ms: typeof step.duration_ms === "number" ? step.duration_ms : null,
    usage: (step.usage as StepExecution["usage"]) || null,
    tools_used: Array.isArray(step.tools_used) ? (step.tools_used as ToolUseEntry[]) : [],
    turns: typeof step.turns === "number" ? step.turns : null,
    response_content: typeof step.content === "string" ? step.content : null,
    error: typeof step.error === "string" ? step.error : null,
  }));
}

export default function ArticleDetailPage() {
  const { runId = "" } = useParams();
  const [article, setArticle] = useState<CompletedArticle | null>(null);
  const [runSteps, setRunSteps] = useState<StepExecution[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<{ kind: "step" | "workspace"; name: string } | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    void api
      .getArticle(runId)
      .then((data) => setArticle(data.article))
      .catch((e: Error) => setError(e.message));
    void api
      .getRun(runId)
      .then((detail) => setRunSteps(detail.steps))
      .catch(() => {
        /* fall back to manifest-only stats */
      });
  }, [runId]);

  const hasContent = article?.has_content ?? Boolean(article?.body_markdown?.trim());
  const stepFiles = article?.step_files ?? [];
  const workspaceFiles = article?.workspace_files ?? [];

  const openStepFile = (filename: string) => {
    setOpenFile({ kind: "step", name: filename });
    setFileContent(null);
    setFileError(null);
    void api
      .getArticleStepFile(runId, filename)
      .then((result) => setFileContent(result.content))
      .catch((e: Error) => setFileError(e.message));
  };

  const openWorkspaceFile = (path: string) => {
    setOpenFile({ kind: "workspace", name: path });
    setFileContent(null);
    setFileError(null);
    void api
      .getArticleWorkspaceFile(runId, path)
      .then((result) => setFileContent(result.content))
      .catch((e: Error) => setFileError(e.message));
  };

  if (error) return <p className="error">{error}</p>;
  if (!article) return <p>Loading artifact…</p>;

  const steps = runSteps.length > 0 ? runSteps : manifestSteps(article.manifest);

  return (
    <section className="card">
      <p>
        <Link to="/articles">← All artifacts</Link>
      </p>
      <h2>{article.title || "Untitled artifact"}</h2>
      <p className="hint">
        Model used: {article.model || "—"} · In {formatTokenCount(article.stats?.input_tokens)} · Out{" "}
        {formatTokenCount(article.stats?.output_tokens)} · Total {formatTokenCount(article.stats?.total_tokens)}
      </p>
      <p className="hint">
        Run{" "}
        {article.run_exists ? (
          <Link to={`/runs/${article.run_id}`}>{article.run_id}</Link>
        ) : (
          <span>{article.run_id}</span>
        )}
        {article.created_at ? ` · ${new Date(article.created_at).toLocaleString()}` : ""}
      </p>
      {!hasContent && (
        <p className="error">
          This artifact has no saved article body. Check the step output files below — the content may only have been
          written to disk.
        </p>
      )}
      {steps.length > 0 && (
        <RunProgressPanel
          title="Pipeline breakdown"
          status="completed"
          statusLabel="Completed"
          steps={steps}
          defaultOpen
        />
      )}
      {(stepFiles.length > 0 || workspaceFiles.length > 0) && (
        <div className="run-step-files">
          <h3>Files from this run</h3>
          {stepFiles.length > 0 && (
            <>
              <p className="hint">Step outputs saved when “Save response to disk” was enabled.</p>
              <ul className="run-step-file-list">
                {stepFiles.map((file) => (
                  <li key={file.name}>
                    <button type="button" className="secondary" onClick={() => openStepFile(file.name)}>
                      {file.name}
                    </button>
                    <span className="hint">{file.size_bytes} bytes</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {workspaceFiles.length > 0 && (
            <>
              <p className="hint">Workspace files written by agent tools during the run.</p>
              <ul className="run-step-file-list">
                {workspaceFiles.map((file) => (
                  <li key={file.path}>
                    <button type="button" className="secondary" onClick={() => openWorkspaceFile(file.path)}>
                      {file.path}
                    </button>
                    <span className="hint">{file.size_bytes} bytes</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {openFile && (
            <div className="run-step-file-preview">
              <div className="run-step-file-preview-head">
                <strong>{openFile.name}</strong>
                <button type="button" className="secondary" onClick={() => setOpenFile(null)}>
                  Close
                </button>
              </div>
              {fileError && <p className="error">{fileError}</p>}
              {fileContent === null && !fileError && <p className="hint">Loading file…</p>}
              {fileContent !== null && <pre>{fileContent}</pre>}
            </div>
          )}
        </div>
      )}
      {hasContent ? (
        <article className="article-body">
          <pre>{article.body_markdown}</pre>
        </article>
      ) : (
        stepFiles.length === 0 &&
        workspaceFiles.length === 0 && (
          <p className="hint">No article text or saved files were found for this run.</p>
        )
      )}
    </section>
  );
}
