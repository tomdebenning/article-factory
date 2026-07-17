import type { PullerInfo } from "../api";
import { pullerStatusDetail } from "./pullers";

export function isLocalStaffPuller(puller: PullerInfo): boolean {
  const name = puller.puller_name.toLowerCase();
  return /local|gpu|mac|desktop|laptop|127\.0\.0\.1|localhost|workstation|studio/.test(name);
}

export function groupStaffPullers(pullers: PullerInfo[]): { local: PullerInfo[]; wire: PullerInfo[] } {
  const local: PullerInfo[] = [];
  const wire: PullerInfo[] = [];
  for (const puller of [...pullers].sort((a, b) => a.puller_name.localeCompare(b.puller_name))) {
    if (isLocalStaffPuller(puller)) {
      local.push(puller);
    } else {
      wire.push(puller);
    }
  }
  return { local, wire };
}

export function pullerCard(puller: PullerInfo) {
  const running = puller.status === "busy" || Boolean(puller.current_task);
  return (
    <div
      key={puller.puller_name}
      className={`puller-status-card${running ? " puller-status-card--running" : ""}`}
    >
      <span className="puller-status-name">{puller.puller_name}</span>
      <span className="puller-status-detail">{pullerStatusDetail(puller)}</span>
    </div>
  );
}
