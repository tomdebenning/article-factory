export function formatTokenCount(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return "—";
  }
  return value.toLocaleString();
}
