import { useEffect, useMemo, useState } from "react";
import { api, type PullerInfo } from "../api";
import { activePullers, modelsFromActivePullers } from "../utils/pullers";

type Props = {
  model: string;
  onModelChange: (model: string) => void;
  label?: string;
  hint?: string;
};

export default function ModelSelectFields({
  model,
  onModelChange,
  label = "Model",
  hint = "Puller is assigned automatically from active pullers when each topic starts.",
}: Props) {
  const [pullers, setPullers] = useState<PullerInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .listPullers()
      .then((data) => setPullers(data.pullers))
      .catch((e: Error) => setError(e.message));
  }, []);

  const modelOptions = useMemo(() => modelsFromActivePullers(pullers), [pullers]);

  const activeCount = useMemo(() => activePullers(pullers).length, [pullers]);

  return (
    <div className="model-select-fields">
      {error && <p className="error">{error}</p>}
      <label>
        {label}
        {modelOptions.length > 0 ? (
          <select value={model} onChange={(e) => onModelChange(e.target.value)}>
            <option value="">— select model —</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        ) : (
          <input
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder="Enter model name from control plane"
          />
        )}
      </label>
      <p className="hint">{hint}</p>
      {activeCount > 0 && (
        <p className="hint">{activeCount} active puller(s) on the control plane.</p>
      )}
      {pullers.length > 0 && activeCount === 0 && (
        <p className="error">No active pullers on the control plane right now.</p>
      )}
      {model && modelOptions.length > 0 && activeCount === 0 && (
        <p className="error">No active pullers available for the selected model.</p>
      )}
      {model && !modelOptions.includes(model) && (
        <p className="error">“{model}” is not available on any active puller.</p>
      )}
    </div>
  );
}
