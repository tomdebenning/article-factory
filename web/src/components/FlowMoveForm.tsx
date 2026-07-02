import { useState } from "react";
import { api } from "../api";

type Props = {
  flowPath: string;
  defaultSlug: string;
  busy?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onMoved: (newPath: string) => void;
  onError: (message: string) => void;
};

export default function FlowMoveForm({
  flowPath,
  defaultSlug,
  busy = false,
  onBusyChange,
  onMoved,
  onError,
}: Props) {
  const [folder, setFolder] = useState("test");
  const [slug, setSlug] = useState(defaultSlug);

  const submit = () => {
    const trimmedFolder = folder.trim();
    const trimmedSlug = slug.trim();
    if (trimmedFolder.startsWith("_templates")) {
      onError("Choose a folder outside _templates.");
      return;
    }
    if (!trimmedSlug) {
      onError("Enter a file slug.");
      return;
    }
    onBusyChange?.(true);
    void api
      .moveFlow({ path: flowPath, folder: trimmedFolder, slug: trimmedSlug })
      .then((result) => onMoved(result.path))
      .catch((e: Error) => {
        const message = e.message;
        if (message === "Not Found" || message === "Flow not found") {
          onError(
            `${message} — the factory API may need a restart to load the move endpoint. Run ./run.sh --local again, then retry.`,
          );
          return;
        }
        onError(message);
      })
      .finally(() => onBusyChange?.(false));
  };

  return (
    <div className="flow-move-form">
      <p className="hint">
        Template flows are not runnable until moved into the library (for example <code>test/</code> or{" "}
        <code>sports/</code>).
      </p>
      <div className="flow-move-form-row">
        <label>
          Folder
          <input
            value={folder}
            disabled={busy}
            placeholder="test"
            onChange={(e) => setFolder(e.target.value)}
          />
        </label>
        <label>
          File slug
          <input value={slug} disabled={busy} onChange={(e) => setSlug(e.target.value)} />
        </label>
        <button type="button" className="primary" disabled={busy} onClick={submit}>
          {busy ? "Moving…" : "Move to library"}
        </button>
      </div>
    </div>
  );
}
