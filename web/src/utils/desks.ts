import type { FlowTreeNode } from "../api";

export type DeskSummary = {
  path: string;
  display_name: string;
  slug: string;
  step_count: number;
  modified_at?: string;
};

export function isTemplateFlowPath(path: string): boolean {
  return path === "_templates" || path.startsWith("_templates/");
}

export function collectDeskPaths(node: FlowTreeNode): string[] {
  const paths: string[] = [];

  const walk = (current: FlowTreeNode) => {
    if (current.type === "file" && current.path && !isTemplateFlowPath(current.path)) {
      paths.push(current.path);
    }
    for (const child of current.children || []) {
      walk(child);
    }
  };

  walk(node);
  return paths;
}

export function deskDetailUrl(path: string): string {
  return `/desks?path=${encodeURIComponent(path)}`;
}

export function deskShiftUrl(path: string, shiftKey: string): string {
  return `/desks/shift?path=${encodeURIComponent(path)}&shift=${encodeURIComponent(shiftKey)}`;
}

export function deskFlowEditUrl(path: string, stepKey?: string): string {
  const base = `/flows/edit?path=${encodeURIComponent(path)}`;
  return stepKey ? `${base}&step=${encodeURIComponent(stepKey)}` : base;
}

export function personaDetailUrl(slug: string): string {
  return `/personas/${encodeURIComponent(slug)}`;
}

export async function loadDeskSummaries(
  getTree: () => Promise<FlowTreeNode>,
  listFlows: (folder: string) => Promise<{ flows: DeskSummary[] }>,
): Promise<DeskSummary[]> {
  const tree = await getTree();
  const paths = collectDeskPaths(tree);
  if (paths.length === 0) {
    return [];
  }

  const byFolder = new Map<string, Set<string>>();
  for (const path of paths) {
    const slash = path.lastIndexOf("/");
    const folder = slash >= 0 ? path.slice(0, slash) : "";
    if (!byFolder.has(folder)) {
      byFolder.set(folder, new Set());
    }
    byFolder.get(folder)!.add(path);
  }

  const summaries: DeskSummary[] = [];
  for (const [folder, folderPaths] of byFolder) {
    const { flows } = await listFlows(folder);
    for (const flow of flows) {
      if (folderPaths.has(flow.path)) {
        summaries.push(flow);
      }
    }
  }

  return summaries.sort((a, b) => a.display_name.localeCompare(b.display_name));
}
