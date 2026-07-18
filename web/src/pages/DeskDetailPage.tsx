import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type FlowDefinition, type Persona, type StandingOrderShift } from "../api";
import { DESK_SHIFT_KEYS } from "../constants/shifts";
import {
  deskDetailUrl,
  deskFlowEditUrl,
  deskShiftUrl,
  isTemplateFlowPath,
  personaDetailUrl,
} from "../utils/desks";

export default function DeskDetailPage() {
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path")?.trim() || "";

  const [flow, setFlow] = useState<FlowDefinition | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [standingOrders, setStandingOrders] = useState<Record<string, StandingOrderShift>>({});
  const [beatBrief, setBeatBrief] = useState("");
  const [editionTopicSlug, setEditionTopicSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [whatMessage, setWhatMessage] = useState<string | null>(null);
  const [savingWhat, setSavingWhat] = useState(false);
  const [savingPool, setSavingPool] = useState(false);

  useEffect(() => {
    if (!path || isTemplateFlowPath(path)) {
      setFlow(null);
      setError(path ? "Template desks cannot be opened from the newsroom dashboard." : null);
      return;
    }

    setError(null);
    void Promise.all([api.getFlow(path), api.listPersonas(), api.listStandingOrders(path)])
      .then(([flowResult, personaResult, ordersResult]) => {
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

  const personaBySlug = useMemo(
    () => new Map(personas.map((persona) => [persona.slug, persona])),
    [personas],
  );

  const reporterPool = flow?.reporter_pool || [];
  const reporters = reporterPool
    .map((slug) => personaBySlug.get(slug))
    .filter((persona): persona is Persona => persona !== undefined);

  const pipelineSteps = [...(flow?.steps || [])].sort((a, b) => a.order - b.order);

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
          A desk defines <strong>what</strong> this beat covers — topic, mission, and assignments. Pipeline prompts
          define <strong>how</strong> each step writes and reviews the work.
        </p>
        <p className="hint desk-path">{path}</p>
      </section>

      <section className="card desk-section desk-section-what">
        <div className="desk-section-head">
          <h3>What to cover</h3>
          <p className="hint">Topic focus, beat mission, and recurring assignments by shift.</p>
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
          Default beat category in The Edition. Shift planning can override this per window. Not the same as an
          assignment line — this is where finished articles appear.
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
        <p className="hint">
          Mission statement for the Assignment Desk when it suggests story angles at T-15.
        </p>
        {whatMessage && <p className="ok">{whatMessage}</p>}
        {error && <p className="error">{error}</p>}
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
              <Link key={shift.key} to={deskShiftUrl(path, shift.key)} className="desk-tile desk-tile-shift">
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

      <section className="card desk-section desk-section-how">
        <div className="desk-section-head">
          <h3>How it is done</h3>
          <p className="hint">Pipeline prompts, writing voices, and editorial roles for this desk.</p>
        </div>

        <div className="desk-page-actions">
          <Link to={deskFlowEditUrl(path)} className="primary">
            Edit pipeline prompts
          </Link>
          <Link to={`/flows/performance?path=${encodeURIComponent(path)}`} className="secondary">
            Pipeline performance
          </Link>
        </div>

        <h4 className="desk-subsection-title">Writing voices</h4>
        {personas.length === 0 ? (
          <div className="desk-empty-panel">
            <p>No reporters yet. Add a writing voice, then assign them to this desk.</p>
            <Link to={`/personas/new?desk=${encodeURIComponent(path)}`} className="desk-tile desk-tile-create">
              Add writing voice
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
                  </li>
                );
              })}
            </ul>
            <p className="hint">
              Checked reporters can be assigned when this desk runs. Configure each person&apos;s writing voice on
              their staff page.
            </p>
          </>
        )}

        {reporters.length > 0 && (
          <div className="desk-button-row">
            {reporters.map((persona) => (
              <Link
                key={persona.slug}
                to={personaDetailUrl(persona.slug) + `?desk=${encodeURIComponent(path)}`}
                className="desk-tile"
              >
                <span className="desk-tile-label">{persona.name}</span>
                <span className="desk-tile-role">Writing voice</span>
                {persona.description ? <span className="desk-tile-meta">{persona.description}</span> : null}
              </Link>
            ))}
          </div>
        )}

        <h4 className="desk-subsection-title">Pipeline steps</h4>
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
      </section>
    </div>
  );
}
