import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import PersonaEditor from "../components/PersonaEditor";
import { api, type Persona } from "../api";
import { deskDetailUrl, addStaffPersonaToDesk } from "../utils/desks";

const emptyPersona = (): Persona => ({
  slug: "",
  name: "",
  description: "",
  style_prompt: "",
});

export default function PersonaDetailPage() {
  const { slug = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const deskPath = searchParams.get("desk");
  const isNew = slug === "new";

  const [persona, setPersona] = useState<Persona | null>(isNew ? emptyPersona() : null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (isNew) {
      setPersona(emptyPersona());
      setError(null);
      return;
    }
    void api
      .getPersona(slug)
      .then(({ persona: loaded }) => {
        setPersona(loaded);
        setError(null);
      })
      .catch((e: Error) => {
        setPersona(null);
        setError(e.message);
      });
  }, [isNew, slug]);

  const remove = () => {
    if (!persona?.slug || !window.confirm(`Delete staff member “${persona.name}”?`)) {
      return;
    }
    setDeleting(true);
    void api
      .deletePersona(persona.slug)
      .then(() => navigate("/personas"))
      .catch((e: Error) => setError(e.message))
      .finally(() => setDeleting(false));
  };

  if (error && !persona) {
    return (
      <section className="card">
        <h2>Staff member unavailable</h2>
        <p className="error">{error}</p>
        <Link to="/personas" className="secondary">
          All desk staff
        </Link>
      </section>
    );
  }

  if (!persona) {
    return (
      <section className="card">
        <p className="hint">Loading staff member…</p>
      </section>
    );
  }

  return (
    <section className="card personas-page">
      <p className="home-eyebrow">
        <Link to="/">Dashboard</Link>
        {" · "}
        <Link to="/personas">Desk staff</Link>
        {deskPath ? (
          <>
            {" · "}
            <Link to={deskDetailUrl(deskPath)}>Desk</Link>
          </>
        ) : null}
      </p>
      <h2>{isNew ? "Add staff member" : persona.name}</h2>
      <p className="hint">
        Writing voice defines <strong>how</strong> the reporter sounds. Beat briefs and assignments on the desk define
        <strong> what </strong>
        to cover.
      </p>
      {message && <p className="ok">{message}</p>}

      <PersonaEditor
        initial={persona}
        editingSlug={isNew ? null : persona.slug}
        deskPath={deskPath}
        onSaved={(saved) => {
          if (isNew && deskPath) {
            void addStaffPersonaToDesk(deskPath, saved.slug)
              .then(() => {
                navigate(deskDetailUrl(deskPath));
              })
              .catch((e: Error) => setError(e.message));
            return;
          }
          setMessage(isNew ? `Created “${saved.name}”.` : `Updated “${saved.name}”.`);
          if (isNew) {
            navigate(`/personas/${encodeURIComponent(saved.slug)}`, { replace: true });
            return;
          }
          setPersona(saved);
        }}
        onCancel={isNew ? () => navigate(deskPath ? deskDetailUrl(deskPath) : "/personas") : undefined}
      />

      {!isNew && (
        <div className="personas-detail-footer">
          <button type="button" className="secondary" disabled={deleting} onClick={remove}>
            {deleting ? "Deleting…" : "Delete staff member"}
          </button>
        </div>
      )}
    </section>
  );
}
