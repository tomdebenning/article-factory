import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";
import { deskDetailUrl } from "../utils/desks";

export default function FlowCreatePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [displayName, setDisplayName] = useState("New desk");
  const [slug, setSlug] = useState("new-desk");
  const [folder, setFolder] = useState(searchParams.get("folder") || "");
  const [editionTopicSlug, setEditionTopicSlug] = useState("");
  const [beatBrief, setBeatBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createDesk = () => {
    const topic = editionTopicSlug.trim();
    const brief = beatBrief.trim();
    if (!topic && !brief) {
      setError("Add an Edition topic or beat brief so this desk can be staffed.");
      return;
    }
    setBusy(true);
    setError(null);
    void api
      .createDesk({
        folder,
        slug: slug.trim() || "new-desk",
        display_name: displayName.trim() || "New desk",
        edition_topic_slug: topic,
        beat_brief: brief,
      })
      .then((result) => {
        notifyFlowsChanged();
        navigate(`${deskDetailUrl(result.path)}&setup=pipeline`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="card">
      <p>
        <Link to="/">← Dashboard</Link>
      </p>
      <h2>Create desk</h2>
      <p className="hint">
        A desk defines <strong>what</strong> you cover — Edition topic, beat mission, and assignments. After creating
        the desk, choose or build a <Link to="/templates">pipeline template</Link> for <strong>how</strong> articles
        are written and reviewed.
      </p>
      {error && <p className="error">{error}</p>}

      <label>
        Display name
        <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>
      <label>
        File slug
        <input value={slug} onChange={(e) => setSlug(e.target.value)} />
      </label>
      <label>
        Folder (optional)
        <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="sports" />
      </label>
      <label>
        Edition topic
        <input
          value={editionTopicSlug}
          onChange={(e) => setEditionTopicSlug(e.target.value)}
          placeholder="e.g. sports, business, ai-news"
        />
      </label>
      <label>
        Beat brief
        <textarea
          rows={4}
          value={beatBrief}
          onChange={(e) => setBeatBrief(e.target.value)}
          placeholder="What this desk covers — leagues, angles, audience, and story types."
        />
      </label>
      <p className="hint">Provide at least one of Edition topic or beat brief.</p>

      <button type="button" className="primary" disabled={busy} onClick={createDesk}>
        {busy ? "Creating…" : "Create desk"}
      </button>
    </section>
  );
}
