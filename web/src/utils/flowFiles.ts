import type { FlowDefinition } from "../api";

export function downloadFlowJson(path: string, flow: FlowDefinition) {
  const blob = new Blob([JSON.stringify(flow, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = path.split("/").pop() || "flow.flow.json";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function readFlowJsonFile(file: File): Promise<FlowDefinition> {
  const text = await file.text();
  const parsed = JSON.parse(text) as FlowDefinition | { flow: FlowDefinition };
  if ("flow" in parsed && parsed.flow && typeof parsed.flow === "object") {
    return parsed.flow;
  }
  return parsed as FlowDefinition;
}

/** Pick settings default when present in the library, otherwise the first available flow. */
export function resolveComposerFlowPath(
  preferred: string | undefined,
  options: { path: string }[],
  fallback = "sports/standard-4-step.flow.json",
): string {
  const candidate = (preferred || fallback).trim();
  if (options.some((option) => option.path === candidate)) {
    return candidate;
  }
  return options[0]?.path || candidate || fallback;
}
