import type { FlowDefinition } from "../api";
import { isTemplateFlowPath } from "./desks";

export const GENERIC_STEP_SYSTEM_PROMPT = "You are a helpful assistant.";
export const PLACEHOLDER_PIPELINE_PROMPT =
  "Pipeline not configured yet. Apply a pipeline template from the desk page.";

export type PipelineTemplateSummary = {
  path: string;
  display_name: string;
  slug: string;
  step_count: number;
  modified_at?: string;
  version_count?: number;
  latest_version?: {
    id: number;
    version_number: number;
    message?: string | null;
  };
};

export function flowIsDesk(flow: Pick<FlowDefinition, "beat_brief" | "edition_topic_slug">): boolean {
  return Boolean(flow.beat_brief?.trim() || flow.edition_topic_slug?.trim());
}

export function deskPipelineNeedsSetup(flow: FlowDefinition): boolean {
  const steps = flow.steps ?? [];
  if (steps.length === 0) {
    return true;
  }
  if (steps.some((step) => step.system_prompt.trim() === PLACEHOLDER_PIPELINE_PROMPT)) {
    return true;
  }
  const genericCount = steps.filter(
    (step) =>
      step.system_prompt.trim() === GENERIC_STEP_SYSTEM_PROMPT &&
      (step.user_prompt_template.trim() === "{{topic}}" || step.user_prompt_template.trim() === ""),
  ).length;
  return genericCount >= Math.ceil(steps.length / 2);
}

export function templateEditUrl(path: string, stepKey?: string): string {
  const base = `/templates/edit?path=${encodeURIComponent(path)}`;
  return stepKey ? `${base}&step=${encodeURIComponent(stepKey)}` : base;
}

export function templatePerformanceUrl(path: string): string {
  return `/flows/performance?path=${encodeURIComponent(path)}`;
}

export function isPipelineTemplatePath(
  path: string,
  flow?: Pick<FlowDefinition, "beat_brief" | "edition_topic_slug">,
): boolean {
  if (path.startsWith("test/")) {
    return false;
  }
  if (flow && isTemplateFlowPath(path)) {
    return true;
  }
  if (flow && flowIsDesk(flow) && !isTemplateFlowPath(path)) {
    return false;
  }
  if (isTemplateFlowPath(path)) {
    return true;
  }
  if (flow) {
    return !flowIsDesk(flow);
  }
  return false;
}
