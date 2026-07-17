import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Persona } from "../api";
import { personaDetailUrl } from "../utils/desks";

export default function PersonasPage() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .listPersonas()
      .then((data) => setPersonas(data.personas))
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <section className="card personas-page">
      <div className="dashboard-section-head">
        <div>
          <h2>Desk staff</h2>
          <p className="hint">
            Reporter voices for your desks. Assign staff to a desk from that desk&apos;s reporter pool.
          </p>
        </div>
        <Link to="/personas/new" className="primary">
          Add staff member
        </Link>
      </div>
      {error && <p className="error">{error}</p>}

      {personas.length === 0 ? (
        <div className="desk-empty-panel">
          <p>No desk staff yet.</p>
          <Link to="/personas/new" className="desk-tile desk-tile-create">
            Create your first staff member
          </Link>
        </div>
      ) : (
        <div className="desk-button-row">
          {personas.map((persona) => (
            <Link key={persona.slug} to={personaDetailUrl(persona.slug)} className="desk-tile">
              <span className="desk-tile-label">{persona.name}</span>
              <span className="desk-tile-role">Reporter</span>
              {persona.description ? <span className="desk-tile-meta">{persona.description}</span> : null}
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
