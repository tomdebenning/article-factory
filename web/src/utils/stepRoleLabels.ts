/** Display names for pipeline step keys — internal keys unchanged in storage. */
export const STEP_ROLE_LABELS: Record<string, string> = {
  writer: "Reporter",
  review: "Editor",
  source_finder: "Researcher",
  fact_asserter: "Fact-checker",
};

export function stepRoleLabel(stepKey: string, fallbackLabel?: string | null): string {
  const key = stepKey.trim();
  if (key && STEP_ROLE_LABELS[key]) {
    return STEP_ROLE_LABELS[key];
  }
  const fallback = (fallbackLabel || "").trim();
  if (fallback) {
    return fallback;
  }
  return key.replace(/_/g, " ") || "Step";
}
