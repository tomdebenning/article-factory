import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import PipelineTemplatePicker from "../components/PipelineTemplatePicker";
import DeskTopicWorkbench from "../components/DeskTopicWorkbench";
import DeskQueuePanel, { countDeskQueueItems } from "../components/DeskQueuePanel";
import DeskReviewPanel from "../components/DeskReviewPanel";
import { api, type FlowDefinition, type Persona, type StandingOrderShift } from "../api";
import { DESK_SHIFT_KEYS } from "../constants/shifts";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";
import { deskPipelineNeedsSetup, flowIsDesk, type PipelineTemplateSummary } from "../utils/pipelineTemplates";
import {
  deskDetailUrl,
  deskFlowEditUrl,
  deskShiftUrl,
  isPipelineTemplateSummary,
  isTemplateFlowPath,
  personaDetailUrl,
} from "../utils/desks";

export type DeskTab = "config" | "queue" | "review";

function parseDeskTab(value: string | null): DeskTab {
  if (value === "queue" || value === "review" || value === "config") {
    return value;
  }
  return "queue";
}

function parseDeskShift(value: string | null): string {
  const key = (value || "morning").trim().toLowerCase();
  return DESK_SHIFT_KEYS.some((shift) => shift.key === key) ? key : "morning";
}

export default function DeskDetailPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const path = searchParams.get("path")?.trim() || "";
  const activeTab = parseDeskTab(searchParams.get("tab"));
  const queueShiftKey = parseDeskShift(searchParams.get("shift"));

  const [flow, setFlow] = useState<FlowDefinition | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [standingOrders, setStandingOrders] = useState<Record<string, StandingOrderShift>>({});
  const [beatBrief, setBeatBrief] = useState("");
  const [editionTopicSlug, setEditionTopicSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [whatMessage, setWhatMessage] = useState<string | null>(null);
  const [savingWhat, setSavingWhat] = useState(false);
  const [savingPool, setSavingPool] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [pipelineTemplates, setPipelineTemplates] = useState<PipelineTemplateSummary[]>([]);
  const [selectedTemplatePath, setSelectedTemplatePath] = useState("");
  const [selectedTemplateVersionId, setSelectedTemplateVersionId] = useState<number | "">("");
  const [applyingTemplate, setApplyingTemplate] = useState(false);
  const [pipelineMessage, setPipelineMessage] = useState<string | null>(null);
  const [queueBadgeCount, setQueueBadgeCount] = useState(0);
  const [queueRefreshToken, setQueueRefreshToken] = useState(0);
  const setupPipeline = searchParams.get("setup") === "pipeline";

  const setActiveTab = (tab: DeskTab) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", tab);
    setSearchParams(next, { replace: true });
  };

  const setQueueShiftKey = (shiftKey: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", "queue");
    next.set("shift", shiftKey);
    setSearchParams(next, { replace: true });
  };

  useEffect(() => {
    if (!path || isTemplateFlowPath(path)) {
      setFlow(null);
      setError(path ? "Pipeline templates are edited on the Templates page, not as desks." : null);
      return;
    }

    setError(null);
    void Promise.all([api.getFlow(path), api.listPersonas(), api.listStandingOrders(path)])
      .then(([flowResult, personaResult, ordersResult]) => {
        if (!flowIsDesk(flowResult.flow)) {
          setFlow(null);
          setError(
            `${path} is a pipeline template, not a coverage desk. Open it from Templates or set beat brief / Edition topic first.`,
          );
          return;
        }
        setFlow(flowResult.flow);
        setBeatBrief(flowResult.flow.beat_brief || "");
        setEditionTopicSlug(flowResult.flow.edition_topic_slug || "");
        setPersonas(personaResult.personas);
        const map: Record<string, StandingOrderShift> = {};
        for (const shift of ordersResult.shifts) {
          map[shift.shift_key] = shift;
        }
        setStandingOrders(map);
      })
      .catch((e: Error) => {
        setFlow(null);
        setError(e.message);
      });
  }, [path]);

  useEffect(() => {
    if (!path || isTemplateFlowPath(path)) {
      return;
    }
    const loadBadge = () => {
      void Promise.all([api.listQueue(), api.factoryStatus()])
        .then(([queueResult, status]) => {
          setQueueBadgeCount(
            countDeskQueueItems(path, queueResult.items, status.active_runs || []),
          );
        })
        .catch(() => {
          /* badge optional */
        });
    };
    loadBadge();
    const timer = window.setInterval(loadBadge, 8000);
    return () => window.clearInterval(timer);
  }, [path, queueRefreshToken]);

  useEffect(() => {
    void api
      .listPipelineTemplates()
      .then((data) => setPipelineTemplates(data.templates.filter(isPipelineTemplateSummary)))
      .catch(() => {
        /* templates optional */
      });
  }, []);

  useEffect(() => {
    if (setupPipeline && flow) {
      setActiveTab("config");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setupPipeline, flow?.slug]);

  const reloadStandingOrders = () => {
    if (!path || isTemplateFlowPath(path)) {
      return;
    }
    void api.listStandingOrders(path).then((ordersResult) => {
      const map: Record<string, StandingOrderShift> = {};
      for (const shift of ordersResult.shifts) {
        map[shift.shift_key] = shift;
      }
      setStandingOrders(map);
    });
  };

  const bumpQueue = () => setQueueRefreshToken((value) => value + 1);

  const personaBySlug = useMemo(
    () => new Map(personas.map((persona) => [persona.slug, persona])),
    [personas],
  );

  const reporterPool = flow?.reporter_pool || [];
  const reporters = reporterPool
    .map((slug) => personaBySlug.get(slug))
    .filter((persona): persona is Persona => persona !== undefined);

  const pipelineSteps = [...(flow?.steps || [])].sort((a, b) => a.order - b.order);
  const pipelineNeedsSetup = flow ? deskPipelineNeedsSetup(flow) : false;

  const applySelectedTemplate = () => {
    if (!flow || !path || !selectedTemplatePath) {
      return;
    }
    const template = pipelineTemplates.find((entry) => entry.path === selectedTemplatePath);
    if (
      !pipelineNeedsSetup &&
      !window.confirm(
        `Replace this desk's pipeline with "${template?.display_name ?? selectedTemplatePath}"?\n\nAll steps and prompts on the desk will be replaced. Beat coverage and writing voices are kept.`,
      )
    ) {
      return;
    }
    setApplyingTemplate(true);
    setPipelineMessage(null);
    setError(null);
    void api
      .applyPipelineTemplate({
        path,
        template_path: selectedTemplatePath,
        version_id: selectedTemplateVersionId === "" ? undefined : selectedTemplateVersionId,
      })
      .then(({ flow: saved }) => {
        setFlow(saved);
        setBeatBrief(saved.beat_brief || "");
        setEditionTopicSlug(saved.edition_topic_slug || "");
        setPipelineMessage(`Applied ${template?.display_name ?? "template"} to this desk.`);
        notifyFlowsChanged();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setApplyingTemplate(false));
  };

  const saveWhat = () => {
    if (!flow) {
      return;
    }
    setSavingWhat(true);
    setWhatMessage(null);
    setError(null);
    const updated = {
      ...flow,
      beat_brief: beatBrief.trim(),
      edition_topic_slug: editionTopicSlug.trim(),
    };
    void api
      .saveFlow(path, updated)
      .then(({ flow: saved }) => {
        setFlow(saved);
        setBeatBrief(saved.beat_brief || "");
        setEditionTopicSlug(saved.edition_topic_slug || "");
        setWhatMessage("Desk coverage saved.");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSavingWhat(false));
  };

  const deleteDesk = () => {
    if (!flow || !path) {
      return;
    }
    const label = flow.display_name || path;
    if (
      !window.confirm(
        `Delete desk "${label}"?\n\nThis removes the flow file and cannot be undone. Standing assignments for this desk are not deleted automatically.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    setError(null);
    void api
      .deleteFlow(path)
      .then(() => {
        notifyFlowsChanged();
        navigate("/", { replace: true });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setDeleting(false));
  };

  const toggleReporter = (slug: string, checked: boolean) => {
    if (!flow) {
      return;
    }
    setSavingPool(true);
    setError(null);
    const next = new Set(flow.reporter_pool || []);
    if (checked) {
      next.add(slug);
    } else {
      next.delete(slug);
    }
    const updated = { ...flow, reporter_pool: [...next] };
    void api
      .saveFlow(path, updated)
      .then(({ flow: saved }) => setFlow(saved))
      .catch((e: Error) => setError(e.message))
      .finally(() => setSavingPool(false));
  };

  if (!path) {
    return (
      <section className="card">
        <h2>Desk</h2>
        <p className="hint">Choose a desk from the home dashboard.</p>
        <Link to="/" className="secondary">
          Back to dashboard
        </Link>
      </section>
    );
  }

  if (error && !flow) {
    return (
      <section className="card">
        <h2>Desk unavailable</h2>
        <p className="error">{error}</p>
        <Link to="/" className="secondary">
          Back to dashboard
        </Link>
      </section>
    );
  }

  if (!flow) {
    return (
      <section className="card">
        <p className="hint">Loading desk…</p>
      </section>
    );
  }

  return (
    <div className="desk-page">
      <section className="card desk-page-hero">
        <p className="home-eyebrow">
          <Link to="/">Dashboard</Link>
          {" · Desk"}
        </p>
        <h2>{flow.display_name}</h2>
        <p className="hint desk-model-copy">
          Configure the beat, queue story assignments, and review how each shift performs.
        </p>
        <p className="hint desk-path">{path}</p>
        <div className="desk-page-actions">
          <button
            type="button"
            className="secondary run-delete-button"
            disabled={deleting || savingWhat || savingPool}
            onClick={deleteDesk}
          >
            {deleting ? "Deleting…" : "Delete desk"}
          </button>
        </div>
      </section>

      <div className="desk-tabs-row">
        <div className="active-tabs desk-tabs" role="tablist" aria-label="Desk sections">
          <button
            type="button"
            role="tab"
            className={`active-tab${activeTab === "config" ? " is-selected" : ""}`}
            aria-selected={activeTab === "config"}
            onClick={() => setActiveTab("config")}
          >
            Config
          </button>
          <button
            type="button"
            role="tab"
            className={`active-tab${activeTab === "queue" ? " is-selected" : ""}`}
            aria-selected={activeTab === "queue"}
            onClick={() => setActiveTab("queue")}
          >
            Current Queue
            {queueBadgeCount > 0 ? <span className="active-tab-badge">{queueBadgeCount}</span> : null}
          </button>
          <button
            type="button"
            role="tab"
            className={`active-tab${activeTab === "review" ? " is-selected" : ""}`}
            aria-selected={activeTab === "review"}
            onClick={() => setActiveTab("review")}
          >
            Review
          </button>
        </div>
      </div>

      {activeTab === "config" && (
        <section className="card desk-section desk-section-what">
          <div className="desk-section-head">
            <h3>What to cover</h3>
            <p className="hint">Topic focus, beat mission, and shift standing-assignment links.</p>
          </div>

          <label>
            Edition topic
            <input
              value={editionTopicSlug}
              onChange={(e) => setEditionTopicSlug(e.target.value)}
              placeholder="e.g. sports, business, ai-news"
            />
          </label>
          <p className="hint">
            Default beat category in The Edition. Shift planning can override this per window.
          </p>

          <label>
            Beat brief
            <textarea
              rows={4}
              value={beatBrief}
              onChange={(e) => setBeatBrief(e.target.value)}
              placeholder="What this desk covers — leagues, angles, audience, and story types."
            />
          </label>
          <p className="hint">Mission statement for the Assignment Desk when it suggests story angles at T-15.</p>
          {whatMessage && <p className="ok">{whatMessage}</p>}
          {error && activeTab === "config" && <p className="error">{error}</p>}
          <button type="button" className="primary" disabled={savingWhat} onClick={saveWhat}>
            {savingWhat ? "Saving…" : "Save coverage"}
          </button>

          <h4 className="desk-subsection-title">Shifts</h4>
          <div className="desk-button-row">
            {DESK_SHIFT_KEYS.map((shift) => {
              const order = standingOrders[shift.key];
              const assignmentCount = order?.topics?.filter(Boolean).length ?? 0;
              const target = order?.target_count;
              return (
                <Link
                  key={shift.key}
                  to={deskDetailUrl(path, { tab: "queue", shift: shift.key })}
                  className="desk-tile desk-tile-shift"
                >
                  <span className="desk-tile-label">{shift.label}</span>
                  <span className="desk-tile-role">Standing assignments</span>
                  <span className="desk-tile-meta">
                    {assignmentCount > 0
                      ? `${assignmentCount} recurring assignment${assignmentCount === 1 ? "" : "s"}`
                      : "No standing assignments yet"}
                    {target != null ? ` · target ${target}` : ""}
                  </span>
                </Link>
              );
            })}
          </div>
        </section>
      )}

      {activeTab === "config" && (
        <section className="card desk-section desk-section-how">
          <div className="desk-section-head">
            <h3>How it is done</h3>
            <p className="hint">
              Staff personas supply writing voice; pipeline templates supply steps, rubrics, and review logic.
            </p>
          </div>

          <h4 className="desk-subsection-title">Desk staff</h4>
          <p className="hint">
            Assign staff personas to this desk. At run time, the selected reporter&apos;s writing voice is merged into
            the writer step.
          </p>
          {reporterPool.length === 0 && !pipelineNeedsSetup && (
            <p className="error">Assign at least one staff persona before running this desk.</p>
          )}
          {personas.length === 0 ? (
            <div className="desk-empty-panel">
              <p>No staff personas yet. Create desk staff first, then assign them here.</p>
              <Link to={`/personas/new?desk=${encodeURIComponent(path)}`} className="desk-tile desk-tile-create">
                Add staff member
              </Link>
            </div>
          ) : (
            <>
              <ul className="flow-reporter-pool-list">
                {personas.map((persona) => {
                  const checked = reporterPool.includes(persona.slug);
                  return (
                    <li key={persona.slug}>
                      <label>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={savingPool}
                          onChange={(e) => toggleReporter(persona.slug, e.target.checked)}
                        />
                        <Link to={personaDetailUrl(persona.slug) + `?desk=${encodeURIComponent(path)}`}>
                          {persona.name}
                        </Link>
                      </label>
                      {persona.description ? (
                        <span className="hint flow-reporter-pool-desc">{persona.description}</span>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              <Link to={`/personas/new?desk=${encodeURIComponent(path)}`} className="secondary">
                Add staff member
              </Link>
            </>
          )}

          <h4 className="desk-subsection-title">Pipeline</h4>
          {(pipelineNeedsSetup || setupPipeline) && (
            <div className="desk-empty-panel desk-pipeline-setup">
              <p>
                <strong>{pipelineNeedsSetup ? "Set up the pipeline." : "Apply a pipeline template."}</strong> Pick an
                existing template or <Link to="/templates/new">create a new template</Link> first.
              </p>
            </div>
          )}

          <PipelineTemplatePicker
            templates={pipelineTemplates}
            selectedPath={selectedTemplatePath}
            selectedVersionId={selectedTemplateVersionId}
            applying={applyingTemplate}
            onSelectPath={setSelectedTemplatePath}
            onSelectVersion={setSelectedTemplateVersionId}
            onApply={applySelectedTemplate}
          />

          {pipelineMessage && <p className="ok">{pipelineMessage}</p>}

          {!pipelineNeedsSetup && (
            <div className="desk-page-actions">
              <Link to={deskFlowEditUrl(path)} className="primary">
                Edit desk pipeline prompts
              </Link>
            </div>
          )}

          <h4 className="desk-subsection-title">Pipeline steps</h4>
          {pipelineSteps.length === 0 ? (
            <div className="desk-empty-panel">
              <p>No steps yet.</p>
              <Link to={deskFlowEditUrl(path)} className="primary">
                Add pipeline steps
              </Link>
            </div>
          ) : (
            <div className="desk-button-row">
              {pipelineSteps.map((step) => (
                <Link
                  key={step.step_id}
                  to={deskFlowEditUrl(path, step.step_key)}
                  className="desk-tile desk-tile-role-card"
                >
                  <span className="desk-tile-label">{step.label}</span>
                  <span className="desk-tile-role">Prompts</span>
                  <span className="desk-tile-meta">Step {step.order}</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      )}

      {activeTab === "queue" && (
        <section className="card desk-section desk-section-queue">
          <div className="desk-section-head">
            <h3>Current Queue</h3>
            <p className="hint">Generate topics for this desk and manage what is running or waiting.</p>
          </div>
          {error && <p className="error">{error}</p>}
          <DeskTopicWorkbench
            deskPath={path}
            editionTopicSlug={editionTopicSlug}
            staffedPersonas={reporters}
            shiftKey={queueShiftKey}
            onShiftKeyChange={setQueueShiftKey}
            standingOrders={standingOrders}
            onStandingOrdersChanged={reloadStandingOrders}
            onQueueChanged={bumpQueue}
          />
          <DeskQueuePanel deskPath={path} refreshToken={queueRefreshToken} />
        </section>
      )}

      {activeTab === "review" && (
        <section className="card desk-section desk-section-review">
          <div className="desk-section-head">
            <h3>Review</h3>
          </div>
          <DeskReviewPanel deskPath={path} standingOrders={standingOrders} />
        </section>
      )}
    </div>
  );
}
