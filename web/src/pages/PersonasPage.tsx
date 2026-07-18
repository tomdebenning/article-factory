import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Persona } from "../api";
import { personaDetailUrl } from "../utils/desks";

export default function PersonasPage() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busySlug, setBusySlug] = useState<string | null>(null);

  const reload = () => {
    void api
      .listPersonas()
      .then((data) => {
        setPersonas(data.personas);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    reload();
  }, []);

  const deleteStaff = (persona: Persona) => {
    if (!window.confirm(`Delete staff member "${persona.name}"?\n\nThis cannot be undone.`)) {
      return;
    }
    setBusySlug(persona.slug);
    setError(null);
    setMessage(null);
    void api
      .deletePersona(persona.slug)
      .then(() => {
        setMessage(`Deleted "${persona.name}".`);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusySlug(null));
  };

  return (
    <section className="card personas-page">
      <div className="dashboard-section-head">
        <div>
          <h2>Desk staff</h2>
          <p className="hint">
            Staff personas hold each reporter&apos;s writing voice. Assign them to a desk from the desk page — at run
            time their voice is merged into the writer step. Beat briefs and assignments define{" "}
            <strong>what</strong> to cover.
          </p>
        </div>
        <Link to="/personas/new" className="primary">
          Add staff member
        </Link>
      </div>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      {personas.length === 0 ? (
        <div className="desk-empty-panel">
          <p>No desk staff yet.</p>
          <Link to="/personas/new" className="desk-tile desk-tile-create">
            Create your first staff member
          </Link>
        </div>
      ) : (
        <ul className="flow-file-list">
          {personas.map((persona) => (
            <li key={persona.slug} className="flow-file-list-item">
              <div className="flow-file-list-main">
                <Link to={personaDetailUrl(persona.slug)}>
                  <strong>{persona.name}</strong>
                </Link>
                <span className="hint">{persona.slug}</span>
                {persona.description ? <span className="hint">{persona.description}</span> : null}
              </div>
              <div className="flow-file-list-actions">
                <Link to={personaDetailUrl(persona.slug)} className="secondary">
                  Edit
                </Link>
                <button
                  type="button"
                  className="secondary run-delete-button"
                  disabled={busySlug === persona.slug}
                  onClick={() => deleteStaff(persona)}
                >
                  {busySlug === persona.slug ? "Deleting…" : "Delete"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
