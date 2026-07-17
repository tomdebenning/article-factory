import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type FlowDefinition, type Persona, type StandingOrderShift } from "../api";
import { DESK_SHIFT_KEYS } from "../constants/shifts";
import {
  deskFlowEditUrl,
  deskShiftUrl,
  isTemplateFlowPath,
  loadDeskSummaries,
  personaDetailUrl,
  type DeskSummary,
} from "../utils/desks";

function stepRoleLabel(stepKey: string, label: string): string {
  if (stepKey === "writer") {
    return "Reporter";
  }
  return label;
}

export default function DeskDetailPage() {
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path")?.trim() || "";

  const [flow, setFlow] = useState<FlowDefinition | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [standingOrders, setStandingOrders] = useState<Record<string, StandingOrderShift>>({});
  const [error, setError] = useState<string | null>(null);

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

  if (error) {
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
        {flow.beat_brief ? <p className="desk-brief">{flow.beat_brief}</p> : null}
        <p className="hint desk-path">{path}</p>
        <div className="desk-page-actions">
          <Link to={deskFlowEditUrl(path)} className="secondary">
            Edit pipeline
          </Link>
          <Link to={`/flows/performance?path=${encodeURIComponent(path)}`} className="secondary">
            Performance
          </Link>
        </div>
      </section>

      <section className="card desk-section">
        <div className="desk-section-head">
          <h3>Staff</h3>
          <p className="hint">Reporters assigned to this desk and the editorial roles in the pipeline.</p>
        </div>

        <h4 className="desk-subsection-title">Reporters</h4>
        {reporters.length === 0 ? (
          <div className="desk-empty-panel">
            <p>No reporters on this desk yet.</p>
            <div className="desk-button-row">
              <Link to="/personas/new" className="desk-tile desk-tile-create">
                Add staff member
              </Link>
              <Link to={deskFlowEditUrl(path)} className="secondary">
                Assign reporters on desk
              </Link>
            </div>
          </div>
        ) : (
          <div className="desk-button-row">
            {reporters.map((persona) => (
              <Link key={persona.slug} to={personaDetailUrl(persona.slug) + `?desk=${encodeURIComponent(path)}`} className="desk-tile">
                <span className="desk-tile-label">{persona.name}</span>
                <span className="desk-tile-role">Reporter</span>
                {persona.description ? <span className="desk-tile-meta">{persona.description}</span> : null}
              </Link>
            ))}
            <Link to="/personas/new" className="desk-tile desk-tile-create">
              <span className="desk-tile-label">Add staff</span>
              <span className="desk-tile-role">New reporter</span>
            </Link>
          </div>
        )}

        <h4 className="desk-subsection-title">Editorial roles</h4>
        <div className="desk-button-row">
          {pipelineSteps.map((step) => (
            <Link
              key={step.step_id}
              to={deskFlowEditUrl(path, step.step_key)}
              className="desk-tile desk-tile-role-card"
            >
              <span className="desk-tile-label">{step.label}</span>
              <span className="desk-tile-role">{stepRoleLabel(step.step_key, step.label)}</span>
              <span className="desk-tile-meta">Step {step.order}</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="card desk-section">
        <div className="desk-section-head">
          <h3>Shifts</h3>
          <p className="hint">Standing orders and recurring topics for each shift window on this desk.</p>
        </div>
        <div className="desk-button-row">
          {DESK_SHIFT_KEYS.map((shift) => {
            const order = standingOrders[shift.key];
            const topicCount = order?.topics?.filter(Boolean).length ?? 0;
            const target = order?.target_count;
            return (
              <Link key={shift.key} to={deskShiftUrl(path, shift.key)} className="desk-tile desk-tile-shift">
                <span className="desk-tile-label">{shift.label}</span>
                <span className="desk-tile-role">Shift config</span>
                <span className="desk-tile-meta">
                  {topicCount > 0
                    ? `${topicCount} standing topic${topicCount === 1 ? "" : "s"}`
                    : "No standing topics yet"}
                  {target != null ? ` · target ${target}` : ""}
                </span>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
