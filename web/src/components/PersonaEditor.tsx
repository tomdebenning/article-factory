import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Persona } from "../api";

type Props = {
  initial: Persona;
  editingSlug: string | null;
  deskPath?: string | null;
  onSaved: (persona: Persona) => void;
  onCancel?: () => void;
};

export default function PersonaEditor({ initial, editingSlug, deskPath, onSaved, onCancel }: Props) {
  const [draft, setDraft] = useState<Persona>({ ...initial });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = () => {
    const name = draft.name.trim();
    const style_prompt = draft.style_prompt.trim();
    if (!name) {
      setError("Enter a staff member name.");
      return;
    }
    if (!style_prompt) {
      setError("Enter a writing voice prompt.");
      return;
    }

    setBusy(true);
    setError(null);

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
      .then(({ persona }) => onSaved(persona))
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="personas-editor">
      {deskPath && (
        <p className="hint">
          Staff for <Link to={`/desks?path=${encodeURIComponent(deskPath)}`}>{deskPath}</Link>
        </p>
      )}
      {error && <p className="error">{error}</p>}
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
        Slug {editingSlug ? "(change with care)" : "(optional)"}
        <input
          value={draft.slug}
          disabled={busy || Boolean(editingSlug)}
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
        Writing voice
        <textarea
          rows={8}
          value={draft.style_prompt}
          disabled={busy}
          placeholder="How this reporter writes — tone, rhythm, audience, and style constraints."
          onChange={(e) => setDraft({ ...draft, style_prompt: e.target.value })}
        />
      </label>
      <div className="personas-editor-actions">
        <button type="button" className="primary" disabled={busy} onClick={save}>
          {busy ? "Saving…" : editingSlug ? "Save changes" : "Add staff member"}
        </button>
        {onCancel && (
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
