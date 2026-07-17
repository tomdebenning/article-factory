export type SixHourSlot = "night" | "morning" | "afternoon" | "evening";

export const SIX_HOUR_SLOT_ORDER: SixHourSlot[] = ["night", "morning", "afternoon", "evening"];

export const SIX_HOUR_SLOT_LABEL: Record<SixHourSlot, string> = {
  night: "Night Shift · 12 AM – 6 AM UTC",
  morning: "Morning Shift · 6 AM – 12 PM UTC",
  afternoon: "Afternoon Shift · 12 PM – 6 PM UTC",
  evening: "Evening Shift · 6 PM – 12 AM UTC",
};

export type TimeSlot<T> = {
  slot: SixHourSlot;
  label: string;
  items: T[];
  itemCount: number;
};

export type TimeSlotDay<T> = {
  dayKey: string;
  label: string;
  slots: TimeSlot<T>[];
  itemCount: number;
};

export function getSixHourSlot(date: Date): SixHourSlot {
  const hour = date.getHours();
  if (hour < 6) {
    return "night";
  }
  if (hour < 12) {
    return "morning";
  }
  if (hour < 18) {
    return "afternoon";
  }
  return "evening";
}

export function dayKeyFromDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function formatDayLabel(key: string): string {
  const [year, month, day] = key.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export function parseTimestamp(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const normalized = iso.includes("T") ? iso : iso.replace(" ", "T");
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatItemTime(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function groupByDayAndSixHourSlot<T>(
  items: T[],
  timestamp: (item: T) => string | null | undefined,
  compareItems?: (a: T, b: T) => number,
): TimeSlotDay<T>[] {
  const days = new Map<string, Map<SixHourSlot, T[]>>();

  for (const item of items) {
    const when = parseTimestamp(timestamp(item));
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
      daySlots.set(slot, []);
    }
    daySlots.get(slot)!.push(item);
  }

  return [...days.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([key, slotMap]) => {
      const slots: TimeSlot<T>[] = SIX_HOUR_SLOT_ORDER.flatMap((slot) => {
        const slotItems = slotMap.get(slot);
        if (!slotItems || slotItems.length === 0) {
          return [];
        }
        const sortedItems = compareItems ? [...slotItems].sort(compareItems) : slotItems;
        return [
          {
            slot,
            label: SIX_HOUR_SLOT_LABEL[slot],
            items: sortedItems,
            itemCount: sortedItems.length,
          },
        ];
      });
      const itemCount = slots.reduce((sum, slotRow) => sum + slotRow.itemCount, 0);
      return {
        dayKey: key,
        label: formatDayLabel(key),
        slots,
        itemCount,
      };
    });
}
