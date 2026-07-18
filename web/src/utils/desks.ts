import { api, type FlowTreeNode } from "../api";

export type DeskSummary = {
  path: string;
  display_name: string;
  slug: string;
  step_count: number;
  beat_brief?: string;
  edition_topic_slug?: string;
  modified_at?: string;
};

export function isTemplateFlowPath(path: string): boolean {
  return path === "_templates" || path.startsWith("_templates/");
}

export function isCoverageDesk(
  desk: Pick<DeskSummary, "beat_brief" | "edition_topic_slug">,
): boolean {
  return Boolean(desk.beat_brief?.trim() || desk.edition_topic_slug?.trim());
}

/** Coverage desks outside the template library (_templates/). */
export function isOperationalDesk(
  entry: Pick<DeskSummary, "path" | "beat_brief" | "edition_topic_slug">,
): boolean {
  if (isTemplateFlowPath(entry.path)) {
    return false;
  }
  return isCoverageDesk(entry);
}

/** Pipeline templates: library files or flows without operational desk metadata. */
export function isPipelineTemplateSummary(
  entry: Pick<DeskSummary, "path" | "beat_brief" | "edition_topic_slug">,
): boolean {
  if (entry.path.startsWith("test/")) {
    return false;
  }
  if (isOperationalDesk(entry)) {
    return false;
  }
  if (isTemplateFlowPath(entry.path)) {
    return true;
  }
  return !isCoverageDesk(entry);
}

export function deskCoverageTitle(desk: DeskSummary): string {
  if (desk.edition_topic_slug?.trim()) {
    const slug = desk.edition_topic_slug.trim();
    return slug
      .split("-")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }
  if (desk.beat_brief?.trim()) {
    return desk.display_name;
  }
  return desk.display_name;
}

export function deskCoverageSubtitle(desk: DeskSummary): string {
  if (desk.edition_topic_slug?.trim()) {
    return `Edition · ${desk.edition_topic_slug.trim()}`;
  }
  return "Beat desk";
}

export function deskCoverageMeta(desk: DeskSummary): string {
  const brief = desk.beat_brief?.trim();
  if (brief) {
    return brief.length > 96 ? `${brief.slice(0, 93)}…` : brief;
  }
  if (desk.edition_topic_slug?.trim()) {
    return "Add a beat brief to describe what this desk covers.";
  }
  return "Set Edition topic and beat brief on the desk.";
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

export function deskDetailUrl(
  path: string,
  options?: { tab?: "config" | "queue" | "review"; shift?: string },
): string {
  const params = new URLSearchParams({ path });
  if (options?.tab) {
    params.set("tab", options.tab);
  }
  if (options?.shift) {
    params.set("shift", options.shift);
  }
  return `/desks?${params.toString()}`;
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

export async function addStaffPersonaToDesk(deskPath: string, personaSlug: string): Promise<void> {
  const { flow } = await api.getFlow(deskPath);
  const pool = new Set(flow.reporter_pool || []);
  pool.add(personaSlug);
  await api.saveFlow(deskPath, { ...flow, reporter_pool: [...pool] });
}

export async function loadDeskSummaries(
  getTree: () => Promise<FlowTreeNode>,
  listFlows: (
    folder: string,
  ) => Promise<{
    flows: Array<{
      path: string;
      display_name: string;
      slug: string;
      step_count: number;
      beat_brief?: string;
      edition_topic_slug?: string;
      modified_at?: string;
    }>;
  }>,
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

  return summaries
    .filter(isOperationalDesk)
    .sort((a, b) => deskCoverageTitle(a).localeCompare(deskCoverageTitle(b)));
}
