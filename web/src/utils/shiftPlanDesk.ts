export type DeskFillLevel = "empty" | "partial" | "ready";

export function deskAssignmentCount(topics: string[]): number {
  return topics.map((line) => line.trim()).filter(Boolean).length;
}

export function deskFillLevel(topics: string[]): DeskFillLevel {
  const count = deskAssignmentCount(topics);
  if (count === 0) {
    return "empty";
  }
  if (count < 3) {
    return "partial";
  }
  return "ready";
}

export function deskFillLabel(level: DeskFillLevel, count: number): string {
  if (level === "empty") {
    return "No assignments";
  }
  if (level === "partial") {
    return `${count} assignment${count === 1 ? "" : "s"} — need ${3 - count} more`;
  }
  return `${count} assignments ready`;
}
