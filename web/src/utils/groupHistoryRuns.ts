import type { RunSummary } from "../api";
import {
  dayKeyFromDate,
  formatDayLabel,
  formatItemTime,
  getSixHourSlot,
  groupByDayAndSixHourSlot,
  parseTimestamp,
  SIX_HOUR_SLOT_LABEL,
  SIX_HOUR_SLOT_ORDER,
  type SixHourSlot,
  type TimeSlotDay,
} from "./sixHourSlots";

export type { SixHourSlot };
export { SIX_HOUR_SLOT_LABEL, formatDayLabel, formatItemTime as formatRunTime };

export type HistorySlotGroup = {
  queue_name: string;
  flow_path: string;
  model: string;
  runs: RunSummary[];
};

export type HistorySlot = {
  slot: SixHourSlot;
  label: string;
  groups: HistorySlotGroup[];
  runCount: number;
};

export type HistoryDay = {
  dayKey: string;
  label: string;
  slots: HistorySlot[];
  runCount: number;
};

function runTimestamp(run: RunSummary): string | null | undefined {
  return run.finished_at ?? run.started_at;
}

function groupKey(run: RunSummary): string {
  const queue = run.flow_queue_name ?? "Unassigned";
  const flow = run.flow_path ?? "";
  const model = run.selected_model ?? "—";
  return `${queue}\0${flow}\0${model}`;
}

function ensureSlotGroup(
  map: Map<string, HistorySlotGroup>,
  run: RunSummary,
): HistorySlotGroup {
  const key = groupKey(run);
  const existing = map.get(key);
  if (existing) {
    return existing;
  }
  const row: HistorySlotGroup = {
    queue_name: run.flow_queue_name ?? "Unassigned",
    flow_path: run.flow_path ?? "",
    model: run.selected_model ?? "—",
    runs: [],
  };
  map.set(key, row);
  return row;
}

export function groupHistoryRuns(runs: RunSummary[]): HistoryDay[] {
  const days = new Map<
    string,
    Map<SixHourSlot, Map<string, HistorySlotGroup>>
  >();

  for (const run of runs) {
    const when = parseTimestamp(runTimestamp(run));
    if (!when) {
      continue;
    }
    const dayKey = dayKeyFromDate(when);
    const slot = getSixHourSlot(when);

    if (!days.has(dayKey)) {
      days.set(dayKey, new Map());
    }
    const daySlots = days.get(dayKey)!;
    if (!daySlots.has(slot)) {
      daySlots.set(slot, new Map());
    }
    const group = ensureSlotGroup(daySlots.get(slot)!, run);
    group.runs.push(run);
  }

  return [...days.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([key, slotMap]) => {
      const slots: HistorySlot[] = SIX_HOUR_SLOT_ORDER.flatMap((slot) => {
        const groupsMap = slotMap.get(slot);
        if (!groupsMap || groupsMap.size === 0) {
          return [];
        }
        const groups = [...groupsMap.values()].sort((a, b) =>
          a.queue_name.localeCompare(b.queue_name),
        );
        const runCount = groups.reduce((sum, group) => sum + group.runs.length, 0);
        return [
          {
            slot,
            label: SIX_HOUR_SLOT_LABEL[slot],
            groups,
            runCount,
          },
        ];
      });
      const runCount = slots.reduce((sum, slotRow) => sum + slotRow.runCount, 0);
      return {
        dayKey: key,
        label: formatDayLabel(key),
        slots,
        runCount,
      };
    });
}

export { groupByDayAndSixHourSlot };
