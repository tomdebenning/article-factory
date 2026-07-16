import { useState } from "react";
import PullerModelFields from "./PullerModelFields";

type Props = {
  title: string;
  description: string;
  busy?: boolean;
  onClose: () => void;
  onSubmit: (model: string, puller: string) => Promise<void>;
};

export default function PromptImprovementDialog({
  title,
  description,
  busy = false,
  onClose,
  onSubmit,
}: Props) {
  const [model, setModel] = useState("");
  const [puller, setPuller] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    if (!model.trim() || !puller.trim()) {
      setError("Select both a model and a puller.");
      return;
    }
    try {
      await onSubmit(model.trim(), puller.trim());
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Prompt improvement failed");
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-improvement-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="prompt-improvement-title">{title}</h3>
        <p className="hint">{description}</p>
        <PullerModelFields
          model={model}
          puller={puller}
          onModelChange={setModel}
          onPullerChange={setPuller}
        />
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="primary" disabled={busy} onClick={() => void submit()}>
            {busy ? "Starting…" : "Run improvement"}
          </button>
        </div>
      </div>
    </div>
  );
}
