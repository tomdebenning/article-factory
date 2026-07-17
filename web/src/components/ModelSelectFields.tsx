import { useEffect, useMemo, useState } from "react";
import { api, type PullerInfo } from "../api";
import { activePullers, modelsFromActivePullers } from "../utils/pullers";
import { groupStaffPullers, pullerCard } from "../utils/staffing";

type Props = {
  model: string;
  onModelChange: (model: string) => void;
  label?: string;
  hint?: string;
  staffingMode?: boolean;
};

export default function ModelSelectFields({
  model,
  onModelChange,
  label = "Model",
  hint = "Puller is assigned automatically from active pullers when each topic starts.",
  staffingMode = false,
}: Props) {
  const [pullers, setPullers] = useState<PullerInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => {
      void api
        .listPullers()
        .then((data) => {
          setPullers(data.pullers);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    };
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  const modelOptions = useMemo(() => modelsFromActivePullers(pullers), [pullers]);
  const activeCount = useMemo(() => activePullers(pullers).length, [pullers]);
  const { local, wire } = useMemo(() => groupStaffPullers(pullers), [pullers]);
  const fieldLabel = staffingMode ? "Staffing — default model" : label;
  const fieldHint = staffingMode
    ? "Choose the default model for shift dispatch. Pullers are grouped by local hardware vs wire services below."
    : hint;

  return (
    <div className="model-select-fields">
      {error && <p className="error">{error}</p>}
      <label>
        {fieldLabel}
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
      <p className="hint">{fieldHint}</p>
      {staffingMode && pullers.length > 0 && (
        <div className="staffing-groups" aria-label="Staffing">
          {local.length > 0 && (
            <div className="staffing-group">
              <h4>Local</h4>
              <div className="puller-status-grid">{local.map(pullerCard)}</div>
            </div>
          )}
          {wire.length > 0 && (
            <div className="staffing-group">
              <h4>Wire</h4>
              <div className="puller-status-grid">{wire.map(pullerCard)}</div>
            </div>
          )}
        </div>
      )}
      {!staffingMode && pullers.length > 0 && (
        <div className="puller-status-grid" aria-label="Control plane pullers">
          {[...pullers]
            .sort((a, b) => a.puller_name.localeCompare(b.puller_name))
            .map((puller) => pullerCard(puller))}
        </div>
      )}
      {activeCount > 0 && (
        <p className="hint">{activeCount} active puller(s) on the control plane.</p>
      )}
      {pullers.length > 0 && activeCount === 0 && (
        <p className="error">No active pullers on the control plane right now.</p>
      )}
      {model && modelOptions.length > 0 && activeCount === 0 && (
        <p className="error">No active pullers available for the selected model.</p>
      )}
      {model && !modelOptions.includes(model) && modelOptions.length > 0 && (
        <p className="error">“{model}” is not available on any active puller.</p>
      )}
    </div>
  );
}
