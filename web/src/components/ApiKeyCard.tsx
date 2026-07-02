import { useRef, useState } from "react";
import SecretInput from "./SecretInput";
import { copyFromInput, copyToClipboard } from "../utils/copyToClipboard";

type Props = {
  title: string;
  description: string;
  configured: boolean;
  masked: string | null;
  generatedKey: string | null;
  onGenerate: () => Promise<void>;
  generating: boolean;
  browserKey?: string;
  onBrowserKeyChange?: (value: string) => void;
  browserKeyLabel?: string;
  browserKeyHint?: string;
};

export default function ApiKeyCard({
  title,
  description,
  configured,
  masked,
  generatedKey,
  onGenerate,
  generating,
  browserKey,
  onBrowserKeyChange,
  browserKeyLabel,
  browserKeyHint,
}: Props) {
  const keyInputRef = useRef<HTMLInputElement>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const copyKey = async () => {
    if (!generatedKey) {
      return;
    }

    const copied =
      (await copyFromInput(keyInputRef.current)) || (await copyToClipboard(generatedKey));

    setCopyState(copied ? "copied" : "failed");
    window.setTimeout(() => setCopyState("idle"), copied ? 2000 : 3000);
  };

  const copyLabel =
    copyState === "copied" ? "Copied!" : copyState === "failed" ? "Select key above" : "Copy key";

  return (
    <div className="api-key-card">
      <h4>{title}</h4>
      <p className="hint">{description}</p>

      <div className="api-key-status">
        {configured || generatedKey ? (
          <span className="api-key-badge configured">Key configured</span>
        ) : (
          <span className="api-key-badge missing">No key yet</span>
        )}
        {configured && masked && !generatedKey && (
          <span className="hint api-key-masked">Current key: {masked}</span>
        )}
      </div>

      {generatedKey && (
        <div className="generated-key-box">
          <label>
            Copy this key now
            <input ref={keyInputRef} value={generatedKey} readOnly onFocus={(e) => e.target.select()} />
          </label>
          <button
            type="button"
            className={`secondary${copyState === "failed" ? " copy-failed" : ""}`}
            onClick={() => void copyKey()}
          >
            {copyLabel}
          </button>
          {copyState === "failed" && (
            <p className="hint copy-failed-hint">Copy was blocked — the key is selected; press Ctrl+C / Cmd+C.</p>
          )}
        </div>
      )}

      <button type="button" className="secondary" disabled={generating} onClick={() => void onGenerate()}>
        {generating ? "Generating…" : configured ? "Generate new key" : "Generate key"}
      </button>
      {configured && (
        <p className="hint api-key-warning">
          Generating a new key invalidates the previous one. Update any app still using the old key.
        </p>
      )}

      {onBrowserKeyChange && (
        <label className="browser-key-field">
          {browserKeyLabel ?? "Use this key in this browser"}
          <SecretInput value={browserKey ?? ""} onChange={onBrowserKeyChange} />
          {browserKeyHint && <span className="hint">{browserKeyHint}</span>}
        </label>
      )}
    </div>
  );
}
