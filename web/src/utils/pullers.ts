import type { PullerInfo } from "../api";

export function isActivePuller(puller: PullerInfo): boolean {
  return puller.is_active && !puller.is_stale;
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
