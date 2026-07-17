export const DESK_SHIFT_KEYS = [
  { key: "night", label: "Night" },
  { key: "morning", label: "Morning" },
  { key: "afternoon", label: "Afternoon" },
  { key: "evening", label: "Evening" },
] as const;

export type DeskShiftKey = (typeof DESK_SHIFT_KEYS)[number]["key"];

export function deskShiftLabel(shiftKey: string): string {
  return DESK_SHIFT_KEYS.find((shift) => shift.key === shiftKey)?.label ?? shiftKey;
}
