import type { PullerInfo } from "../api";

export function isActivePuller(puller: PullerInfo): boolean {
  return puller.is_active && !puller.is_stale;
}

export function pullerRunningModel(puller: PullerInfo): string | null {
  const model = puller.current_task?.model?.trim();
  return model || null;
}

export function pullerStatusDetail(puller: PullerInfo): string {
  const runningModel = pullerRunningModel(puller);
  if (runningModel) return runningModel;
  if (puller.status === "busy") return "Generating";
  if (!isActivePuller(puller)) return puller.is_stale ? "Stale" : "Offline";
  if (puller.status === "idle") return "Idle";
  return puller.status || "—";
}

export function modelsFromActivePullers(pullers: PullerInfo[]): string[] {
  const names = new Set<string>();
  for (const puller of pullers) {
    if (!isActivePuller(puller)) continue;
    for (const model of puller.supported_models) {
      names.add(model);
    }
  }
  return Array.from(names).sort();
}

export function activePullers(pullers: PullerInfo[]): PullerInfo[] {
  return pullers.filter(isActivePuller);
}

export function pullersForModel(pullers: PullerInfo[], model: string): PullerInfo[] {
  if (!model.trim()) return [];
  return activePullers(pullers).filter(
    (puller) => puller.supported_models.length === 0 || puller.supported_models.includes(model),
  );
}
