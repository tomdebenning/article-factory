import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type FlowDefinition, type FlowStep } from "../api";
import FlowMoveForm from "../components/FlowMoveForm";
import StandingOrdersPanel from "../components/StandingOrdersPanel";
import { downloadFlowJson } from "../utils/flowFiles";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";

function isTemplateFlowPath(path: string): boolean {
  return path === "_templates" || path.startsWith("_templates/");
}

function reorderSteps(steps: FlowStep[]): FlowStep[] {
  return steps.map((step, index) => ({ ...step, order: index + 1 }));
}

function newStep(order: number): FlowStep {
  return {
    step_id: crypto.randomUUID(),
    order,
    step_key: `step_${order}`,
    label: `Step ${order}`,
    system_prompt: "You are a helpful assistant.",
    user_prompt_template: "{{topic}}",
    save_response_to_disk: false,
    loop: null,
    completion: null,
  };
}

function normalizeLastStepCompletion(steps: FlowStep[]): FlowStep[] {
  if (steps.length === 0) return steps;
  return steps.map((step, index) => {
    if (index !== steps.length - 1) {
      return { ...step, completion: null };
    }
    const completion = step.completion || {
      can_complete: true,
      can_loop: steps.length > 1,
      loop_goto_step_id: steps.length > 1 ? steps[0].step_id : null,
    };
    return { ...step, completion };
  });
}

export default function FlowEditorPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path") || "";
  const stepKey = searchParams.get("step") || "";
  const versionIdParam = searchParams.get("version_id");
  const versionId = versionIdParam ? Number(versionIdParam) : null;
  const [flow, setFlow] = useState<FlowDefinition | null>(null);
  const [versionLabel, setVersionLabel] = useState<string | null>(null);
  const [readOnlyVersion, setReadOnlyVersion] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [moving, setMoving] = useState(false);
  const [personas, setPersonas] = useState<Array<{ slug: string; name: string }>>([]);

  useEffect(() => {
    void api.listPersonas().then((data) => setPersonas(data.personas)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!stepKey || !flow) {
      return;
    }
    const target = document.getElementById(`flow-step-${stepKey}`);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [stepKey, flow]);

  useEffect(() => {
    if (!path) return;
    if (versionId && Number.isFinite(versionId)) {
      void api
        .getFlowVersionDetail(versionId)
        .then((data) => {
          if (data.version.flow_path !== path) {
            throw new Error("Version does not match this flow path");
          }
          setFlow(data.version.flow_content);
          setVersionLabel(
            `v${data.version.version_number}${data.version.message ? ` — ${data.version.message}` : ""}`,
          );
          setReadOnlyVersion(true);
        })
        .catch((e: Error) => setError(e.message));
      return;
    }
    setReadOnlyVersion(false);
    setVersionLabel(null);
    void api
      .getFlow(path)
      .then((data) => setFlow(data.flow))
      .catch((e: Error) => setError(e.message));
  }, [path, versionId]);

  const steps = useMemo(() => (flow ? [...flow.steps].sort((a, b) => a.order - b.order) : []), [flow]);
  const earlierSteps = (index: number) => steps.slice(0, index);

  const updateStep = (stepId: string, patch: Partial<FlowStep>) => {
    if (!flow || readOnlyVersion) return;
    setFlow({
      ...flow,
      steps: flow.steps.map((step) => (step.step_id === stepId ? { ...step, ...patch } : step)),
    });
    setMessage(null);
    setError(null);
  };

  const moveStep = (index: number, direction: -1 | 1) => {
    if (!flow) return;
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= steps.length) return;
    const reordered = [...steps];
    const [item] = reordered.splice(index, 1);
    reordered.splice(nextIndex, 0, item);
    setFlow({ ...flow, steps: reorderSteps(reordered) });
  };

  const addStep = () => {
    if (!flow) return;
    const next = newStep(steps.length + 1);
    setFlow({ ...flow, steps: normalizeLastStepCompletion(reorderSteps([...steps, next])) });
  };

  const deleteStep = (stepId: string) => {
    if (!flow || steps.length <= 1) return;
    const remaining = steps.filter((step) => step.step_id !== stepId);
    const cleaned = remaining.map((step) => {
      const loopTarget = step.loop?.goto_step_id;
      const completionTarget = step.completion?.loop_goto_step_id;
      const patch: Partial<FlowStep> = {};
      if (step.loop?.enabled && loopTarget === stepId) {
        patch.loop = { enabled: false, goto_step_id: null };
      }
      if (step.completion?.loop_goto_step_id === stepId) {
        patch.completion = {
          ...step.completion,
          loop_goto_step_id: remaining[0]?.step_id ?? null,
        };
      }
      return Object.keys(patch).length > 0 ? { ...step, ...patch } : step;
    });
    setFlow({ ...flow, steps: normalizeLastStepCompletion(reorderSteps(cleaned)) });
  };

  const save = () => {
    if (!flow || !path || readOnlyVersion) return;
    const payload = {
      ...flow,
      steps: normalizeLastStepCompletion(reorderSteps(steps)),
    };
    setSaving(true);
    setError(null);
    void api
      .saveFlow(path, payload)
      .then((saved) => {
        setFlow(saved.flow);
        setMessage("Desk saved.");
        notifyFlowsChanged();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSaving(false));
  };

  if (!path) {
    return (
      <section className="card">
        <p className="error">Missing flow path.</p>
        <Link to="/flows">Back to desks</Link>
      </section>
    );
  }

  if (!flow) {
    return (
      <section className="card">
        <p>Loading desk…</p>
      </section>
    );
  }

  return (
    <section className="card flow-editor">
      <div className="flow-editor-head">
        <p><Link to="/flows">← All desks</Link></p>
        <div className="flow-editor-head-actions">
          <Link
            to={`/flows/performance?path=${encodeURIComponent(path)}`}
            className="secondary"
          >
            Prompt performance
          </Link>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              void api
                .exportFlow(path)
                .then((data) => downloadFlowJson(data.path, data.flow))
                .catch((e: Error) => setError(e.message));
            }}
          >
            Export JSON
          </button>
        </div>
      </div>
      <h2>{flow.display_name}</h2>
      {readOnlyVersion && versionId && (
        <div className="flow-template-banner">
          <strong>Viewing saved version {versionLabel}</strong>
          <p className="hint">
            This is a read-only snapshot from version history. Apply it to the on-disk flow file to edit or run from
            the file copy.
          </p>
          <button
            type="button"
            className="primary"
            disabled={saving}
            onClick={() => {
              setSaving(true);
              setError(null);
              void api
                .applyFlowVersion(versionId)
                .then((result) => {
                  setMessage(result.message);
                  navigate(`/flows/edit?path=${encodeURIComponent(path)}`);
                })
                .catch((e: Error) => setError(e.message))
                .finally(() => setSaving(false));
            }}
          >
            {saving ? "Applying…" : "Apply to flow file"}
          </button>
        </div>
      )}
      <p className="hint">
        <Link to={`/desks?path=${encodeURIComponent(path)}`}>← Desk overview</Link>
        {" · "}
        <code>{path}</code> · Templates: {"{{topic}}"}, {"{{feedback}}"}, {"{{step_key}}"} (e.g. {"{{writer}}"}).
        Review steps should end with <code>VERDICT: ACCEPT</code> or <code>VERDICT: REJECT</code>.
        Model and puller are chosen on <Link to="/start-flows">Plan a shift</Link>, not in the desk file.
      </p>
      {message && <p className="ok">{message}</p>}
      {error && <p className="error">{error}</p>}
      {isTemplateFlowPath(path) && (
        <div className="flow-template-banner">
          <strong>Template flow</strong>
          <p className="hint">
            This file lives in <code>_templates</code> and will not appear in the Plan a shift dropdown until you move it
            into the library.
          </p>
          <FlowMoveForm
            flowPath={path}
            defaultSlug={flow.slug}
            busy={moving}
            onBusyChange={setMoving}
            onError={setError}
            onMoved={(newPath) => {
              setMessage(`Moved to ${newPath}`);
              notifyFlowsChanged();
              navigate(`/flows/edit?path=${encodeURIComponent(newPath)}`, {
                replace: true,
                state: { flow_path: newPath },
              });
            }}
          />
        </div>
      )}

      <label>
        Display name
        <input value={flow.display_name} onChange={(e) => setFlow({ ...flow, display_name: e.target.value })} />
      </label>

      {!readOnlyVersion && (
        <div className="step-card flow-reporter-pool-card">
          <h3>Beat brief</h3>
          <p className="hint">
            Mission statement for this desk — the Assignment Desk uses it when suggesting story topics at T-15.
          </p>
          <textarea
            rows={4}
            value={flow.beat_brief || ""}
            onChange={(e) => setFlow({ ...flow, beat_brief: e.target.value })}
            placeholder="e.g. Cover college sports in the Pacific Northwest with emphasis on game recaps and athlete profiles."
          />
        </div>
      )}

      {!readOnlyVersion && !isTemplateFlowPath(path) && (
        <StandingOrdersPanel deskPath={path} />
      )}

      {!readOnlyVersion && (
        <div className="step-card flow-reporter-pool-card">
          <h3>Reporter pool</h3>
          <p className="hint">
            Desk staff who can cover the Reporter role on this desk. Assignments pick from this pool when a shift runs
            (round robin or least recently used).
          </p>
          {personas.length === 0 ? (
            <p className="hint">
              No desk staff yet. <Link to="/personas">Create reporters</Link> first.
            </p>
          ) : (
            <ul className="flow-reporter-pool-list">
              {personas.map((persona) => {
                const pool = flow.reporter_pool || [];
                const checked = pool.includes(persona.slug);
                return (
                  <li key={persona.slug}>
                    <label>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          const next = new Set(flow.reporter_pool || []);
                          if (e.target.checked) {
                            next.add(persona.slug);
                          } else {
                            next.delete(persona.slug);
                          }
                          setFlow({ ...flow, reporter_pool: [...next] });
                        }}
                      />
                      {persona.name}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      <label>
        Max iterations (review loops)
        <input
          type="number"
          min={1}
          max={100}
          value={flow.max_iterations}
          onChange={(e) => setFlow({ ...flow, max_iterations: Number(e.target.value) })}
        />
      </label>
      <label>
        Artifact output step (published body comes from this step)
        <select
          value={flow.article_step_id || steps[0]?.step_id || ""}
          onChange={(e) => setFlow({ ...flow, article_step_id: e.target.value })}
        >
          {steps.map((step, index) => (
            <option key={step.step_id} value={step.step_id}>
              Step {index + 1}: {step.label} ({step.step_key})
            </option>
          ))}
        </select>
      </label>

      {steps.map((step, index) => {
        const isFirst = index === 0;
        const isLast = index === steps.length - 1;
        const prior = earlierSteps(index);
        return (
          <div key={step.step_id} id={`flow-step-${step.step_key}`} className="step-card flow-step-card">
            <div className="flow-step-head">
              <h3>
                Step {index + 1}: {step.label}
              </h3>
              <div className="flow-step-actions">
                <button type="button" className="secondary" disabled={index === 0} onClick={() => moveStep(index, -1)}>
                  ↑
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={index === steps.length - 1}
                  onClick={() => moveStep(index, 1)}
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="secondary run-delete-button"
                  disabled={steps.length <= 1}
                  onClick={() => deleteStep(step.step_id)}
                >
                  Delete
                </button>
              </div>
            </div>

            <label>
              Label
              <input value={step.label} onChange={(e) => updateStep(step.step_id, { label: e.target.value })} />
            </label>
            <label>
              Step key (template variable)
              <input
                value={step.step_key}
                onChange={(e) => updateStep(step.step_id, { step_key: e.target.value })}
              />
            </label>

            <div className="flow-step-options">
              <p className="hint flow-tools-note">
                All prompts receive factory tools: write_file, read_file, list_files, web_search, and web_fetch.
                Web search requires a Brave API key in Settings.
              </p>

              <label className="flow-checkbox">
                <input
                  type="checkbox"
                  checked={step.save_response_to_disk}
                  onChange={(e) => updateStep(step.step_id, { save_response_to_disk: e.target.checked })}
                />
                Save response to disk
              </label>

              {!isFirst && !isLast && (
                <>
                  <label className="flow-checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(step.loop?.enabled)}
                      onChange={(e) =>
                        updateStep(step.step_id, {
                          loop: {
                            enabled: e.target.checked,
                            goto_step_id: step.loop?.goto_step_id || prior[0]?.step_id || null,
                          },
                        })
                      }
                    />
                    Loop on VERDICT: REJECT
                  </label>
                  {step.loop?.enabled && (
                    <label className="flow-step-option-field">
                      Loop back to step
                      <select
                        value={step.loop.goto_step_id || ""}
                        onChange={(e) =>
                          updateStep(step.step_id, {
                            loop: { enabled: true, goto_step_id: e.target.value },
                          })
                        }
                      >
                        {prior.map((option, optionIndex) => (
                          <option key={option.step_id} value={option.step_id}>
                            Step {optionIndex + 1}: {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </>
              )}

              {isLast && (
                <>
                  <label className="flow-checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(step.completion?.can_complete ?? true)}
                      onChange={(e) =>
                        updateStep(step.step_id, {
                          completion: {
                            can_complete: e.target.checked,
                            can_loop: step.completion?.can_loop ?? false,
                            loop_goto_step_id: step.completion?.loop_goto_step_id ?? prior[0]?.step_id ?? null,
                          },
                        })
                      }
                    />
                    Can complete run (VERDICT: ACCEPT)
                  </label>
                  <label className="flow-checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(step.completion?.can_loop)}
                      onChange={(e) =>
                        updateStep(step.step_id, {
                          completion: {
                            can_complete: step.completion?.can_complete ?? true,
                            can_loop: e.target.checked,
                            loop_goto_step_id: step.completion?.loop_goto_step_id ?? prior[0]?.step_id ?? null,
                          },
                        })
                      }
                    />
                    Can loop on VERDICT: REJECT
                  </label>
                  {step.completion?.can_loop && (
                    <label className="flow-step-option-field">
                      Loop back to step
                      <select
                        value={step.completion.loop_goto_step_id || ""}
                        onChange={(e) =>
                          updateStep(step.step_id, {
                            completion: {
                              can_complete: step.completion?.can_complete ?? true,
                              can_loop: true,
                              loop_goto_step_id: e.target.value,
                            },
                          })
                        }
                      >
                        {prior.map((option, optionIndex) => (
                          <option key={option.step_id} value={option.step_id}>
                            Step {optionIndex + 1}: {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </>
              )}
            </div>

            <label>
              System prompt
              <textarea
                rows={4}
                value={step.system_prompt}
                onChange={(e) => updateStep(step.step_id, { system_prompt: e.target.value })}
              />
            </label>
            <label>
              User prompt template
              <div className="flow-var-chips">
                {["{{topic}}", "{{feedback}}", ...steps.slice(0, index).map((s) => `{{${s.step_key}}}`)].map((token) => (
                  <button
                    key={`${step.step_id}-${token}`}
                    type="button"
                    className="secondary flow-var-chip"
                    onClick={() =>
                      updateStep(step.step_id, {
                        user_prompt_template: `${step.user_prompt_template}${token}`,
                      })
                    }
                  >
                    + {token}
                  </button>
                ))}
              </div>
              <textarea
                rows={4}
                value={step.user_prompt_template}
                onChange={(e) => updateStep(step.step_id, { user_prompt_template: e.target.value })}
              />
            </label>
          </div>
        );
      })}

      <div className="flow-editor-footer">
        <button type="button" className="secondary" onClick={addStep}>
          Add step
        </button>
        <button type="button" className="primary" disabled={saving || readOnlyVersion} onClick={save}>
          {saving ? "Saving…" : "Save desk"}
        </button>
      </div>
    </section>
  );
}
