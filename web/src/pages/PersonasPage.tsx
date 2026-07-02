import { useEffect, useState } from "react";
import { api, type Persona } from "../api";

const emptyDraft = (): Persona => ({
  slug: "",
  name: "",
  description: "",
  style_prompt: "",
});

export default function PersonasPage() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [draft, setDraft] = useState<Persona>(emptyDraft);
  const [editingSlug, setEditingSlug] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    void api
      .listPersonas()
      .then((data) => setPersonas(data.personas))
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    reload();
  }, []);

  const resetDraft = () => {
    setDraft(emptyDraft());
    setEditingSlug(null);
    setError(null);
  };

  const startEdit = (persona: Persona) => {
    setEditingSlug(persona.slug);
    setDraft({ ...persona });
    setError(null);
    setMessage(null);
  };

  const save = () => {
    const name = draft.name.trim();
    const style_prompt = draft.style_prompt.trim();
    if (!name) {
      setError("Enter a persona name.");
      return;
    }
    if (!style_prompt) {
      setError("Enter a style prompt.");
      return;
    }

    setBusy(true);
    setError(null);
    setMessage(null);

    const payload = {
      name,
      slug: draft.slug.trim() || undefined,
      description: draft.description.trim(),
      style_prompt,
    };

    const request = editingSlug
      ? api.updatePersona(editingSlug, payload)
      : api.createPersona(payload);

    void request
      .then(({ persona }) => {
        setMessage(editingSlug ? `Updated “${persona.name}”.` : `Created “${persona.name}”.`);
        resetDraft();
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const remove = (persona: Persona) => {
    if (!window.confirm(`Delete persona “${persona.name}”?`)) {
      return;
    }
    setBusy(true);
    setError(null);
    void api
      .deletePersona(persona.slug)
      .then(() => {
        if (editingSlug === persona.slug) {
          resetDraft();
        }
        setMessage(`Deleted “${persona.name}”.`);
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="card personas-page">
      <h2>Personas</h2>
      <p className="hint">
        Personas capture writing style and tone. Later you will assign them to flow steps so their
        instructions are merged into each step&apos;s system prompt.
      </p>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      <div className="personas-editor">
        <h3>{editingSlug ? "Edit persona" : "Create persona"}</h3>
        <label>
          Name
          <input
            value={draft.name}
            disabled={busy}
            placeholder="Sports beat reporter"
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <label>
          Slug (optional)
          <input
            value={draft.slug}
            disabled={busy}
            placeholder="sports-beat-reporter"
            onChange={(e) => setDraft({ ...draft, slug: e.target.value })}
          />
        </label>
        <label>
          Short description
          <input
            value={draft.description}
            disabled={busy}
            placeholder="Energetic, fan-friendly coverage"
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          />
        </label>
        <label>
          Style prompt
          <textarea
            rows={8}
            value={draft.style_prompt}
            disabled={busy}
            placeholder="Write in a concise, energetic sports journalism voice. Use active verbs, short paragraphs, and avoid jargon unless explaining it for casual readers."
            onChange={(e) => setDraft({ ...draft, style_prompt: e.target.value })}
          />
        </label>
        <div className="personas-editor-actions">
          <button type="button" className="primary" disabled={busy} onClick={save}>
            {busy ? "Saving…" : editingSlug ? "Save changes" : "Create persona"}
          </button>
          {editingSlug && (
            <button type="button" className="secondary" disabled={busy} onClick={resetDraft}>
              Cancel edit
            </button>
          )}
        </div>
      </div>

      <h3>Saved personas</h3>
      {personas.length === 0 ? (
        <p className="hint">No personas yet. Create one above to define a writing style.</p>
      ) : (
        <ul className="personas-list">
          {personas.map((persona) => (
            <li key={persona.slug} className="personas-list-item">
              <div className="personas-list-main">
                <strong>{persona.name}</strong>
                <span className="hint personas-slug">{persona.slug}</span>
                {persona.description && <p className="hint">{persona.description}</p>}
                <pre className="personas-preview">{persona.style_prompt}</pre>
              </div>
              <div className="personas-list-actions">
                <button type="button" className="secondary" disabled={busy} onClick={() => startEdit(persona)}>
                  Edit
                </button>
                <button type="button" className="secondary" disabled={busy} onClick={() => remove(persona)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
