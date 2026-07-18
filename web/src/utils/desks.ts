import type { FlowTreeNode } from "../api";

export type DeskSummary = {
  path: string;
  display_name: string;
  slug: string;
  step_count: number;
  beat_brief?: string;
  edition_topic_slug?: string;
  modified_at?: string;
};

const PIPELINE_ONLY_SLUGS = new Set(["standard-4-step", "single-writer", "writer-review", "new-desk"]);

export function isTemplateFlowPath(path: string): boolean {
  return path === "_templates" || path.startsWith("_templates/");
}

export function isCoverageDesk(desk: DeskSummary): boolean {
  if (desk.beat_brief?.trim() || desk.edition_topic_slug?.trim()) {
    return true;
  }
  return !PIPELINE_ONLY_SLUGS.has(desk.slug);
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
    .filter(isCoverageDesk)
    .sort((a, b) => deskCoverageTitle(a).localeCompare(deskCoverageTitle(b)));
}
