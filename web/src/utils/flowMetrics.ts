export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function completionRate(row: {
  completion_rate?: number | null;
  completed_count?: number;
  run_count?: number;
}): number | null {
  if (row.completion_rate !== null && row.completion_rate !== undefined) {
    return row.completion_rate;
  }
  const total = row.run_count ?? 0;
  if (total <= 0) return null;
  return (row.completed_count ?? 0) / total;
}

export function firstPassYieldRate(row: {
  first_pass_yield_rate?: number | null;
  first_pass_count?: number;
  run_count?: number;
}): number | null {
  if (row.first_pass_yield_rate !== null && row.first_pass_yield_rate !== undefined) {
    return row.first_pass_yield_rate;
  }
  const total = row.run_count ?? 0;
  if (total <= 0) return null;
  return (row.first_pass_count ?? 0) / total;
}

export function firstPassCompletedRate(row: {
  first_pass_completed_rate?: number | null;
  first_pass_rate?: number | null;
  first_pass_count?: number;
  completed_count?: number;
}): number | null {
  if (row.first_pass_completed_rate !== null && row.first_pass_completed_rate !== undefined) {
    return row.first_pass_completed_rate;
  }
  if (row.first_pass_rate !== null && row.first_pass_rate !== undefined) {
    return row.first_pass_rate;
  }
  const completed = row.completed_count ?? 0;
  if (completed <= 0) return null;
  return (row.first_pass_count ?? 0) / completed;
}

export function num(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  if (digits === 0) return String(Math.round(value));
  return value.toFixed(digits);
}
