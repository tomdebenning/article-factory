import { useEffect, useMemo, useState } from "react";
import { api, type PullerInfo } from "../api";
import { activePullers, modelsFromActivePullers, pullersForModel } from "../utils/pullers";

type Props = {
  model: string;
  puller: string;
  onModelChange: (model: string) => void;
  onPullerChange: (puller: string) => void;
};

export default function PullerModelFields({
  model,
  puller,
  onModelChange,
  onPullerChange,
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
  const pullerOptions = useMemo(() => pullersForModel(pullers, model), [pullers, model]);
  const activeCount = useMemo(() => activePullers(pullers).length, [pullers]);

  useEffect(() => {
    if (!model || pullerOptions.some((item) => item.puller_name === puller)) return;
    onPullerChange(pullerOptions[0]?.puller_name || "");
  }, [model, puller, pullerOptions, onPullerChange]);

  return (
    <div className="model-select-fields">
      {error && <p className="error">{error}</p>}
      <label>
        Model
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
      <label>
        Puller
        {pullerOptions.length > 0 ? (
          <select value={puller} onChange={(e) => onPullerChange(e.target.value)}>
            <option value="">— select puller —</option>
            {pullerOptions.map((item) => (
              <option key={item.puller_name} value={item.puller_name}>
                {item.puller_name}
              </option>
            ))}
          </select>
        ) : (
          <input
            value={puller}
            onChange={(e) => onPullerChange(e.target.value)}
            placeholder="Enter puller name"
          />
        )}
      </label>
      <p className="hint">Choose the control-plane model and puller for this analysis run.</p>
      {activeCount > 0 && <p className="hint">{activeCount} active puller(s) on the control plane.</p>}
      {model && pullerOptions.length === 0 && (
        <p className="error">No active pullers support “{model}”.</p>
      )}
    </div>
  );
}
