import type { FlowTreeNode } from "../api";

export type FlowSelectOption = {
  path: string;
  label: string;
};

export const FLOWS_CHANGED_EVENT = "factory:flows-changed";

export function notifyFlowsChanged() {
  window.dispatchEvent(new Event(FLOWS_CHANGED_EVENT));
}

export function collectFlowFilesFromTree(
  node: FlowTreeNode,
  out: FlowSelectOption[] = [],
): FlowSelectOption[] {
  if (node.type === "file") {
    if (!node.path.startsWith("_templates/")) {
      out.push({ path: node.path, label: node.path });
    }
    return out;
  }
  for (const child of node.children || []) {
    collectFlowFilesFromTree(child, out);
  }
  return out;
}

export function sortFlowSelectOptions(options: FlowSelectOption[]): FlowSelectOption[] {
  return [...options].sort((a, b) => a.path.localeCompare(b.path));
}

export function mergeFlowSelectLabels(
  options: FlowSelectOption[],
  displayNames: Map<string, string>,
): FlowSelectOption[] {
  return options.map((option) => {
    const displayName = displayNames.get(option.path);
    if (!displayName || displayName === option.path) {
      return option;
    }
    return { path: option.path, label: `${displayName} (${option.path})` };
  });
}

export function ensureFlowSelectOption(
  options: FlowSelectOption[],
  path: string | undefined,
): FlowSelectOption[] {
  const trimmed = path?.trim();
  if (!trimmed || options.some((option) => option.path === trimmed)) {
    return options;
  }
  return sortFlowSelectOptions([...options, { path: trimmed, label: trimmed }]);
}
