function parseCsvTopics(content: string): string[] {
  const topics: string[] = [];
  const lines = content.split(/\r?\n/);
  for (const line of lines) {
    if (!line.trim()) continue;
    const first = line.split(",")[0]?.trim().replace(/^"|"$/g, "");
    if (first) topics.push(first);
  }
  return topics;
}

function parseLineTopics(content: string): string[] {
  return content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function parseTopicsFromText(content: string, filename = ""): string[] {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".csv")) {
    return parseCsvTopics(content);
  }
  return parseLineTopics(content);
}

export async function parseTopicsFromFile(file: File): Promise<string[]> {
  const content = await file.text();
  return parseTopicsFromText(content, file.name);
}

export type QueuePresetFile = {
  version?: number;
  name: string;
  slug?: string;
  topic_slug?: string;
  flow_path: string;
  default_model?: string;
  topics: string[];
};

export async function readQueuePresetFile(file: File): Promise<QueuePresetFile> {
  const content = await file.text();
  const data = JSON.parse(content) as QueuePresetFile;
  if (!data.name?.trim()) {
    throw new Error("Preset file is missing a queue name.");
  }
  if (!data.flow_path?.trim()) {
    throw new Error("Preset file is missing a flow path.");
  }
  if (!Array.isArray(data.topics)) {
    throw new Error("Preset file is missing a topics list.");
  }
  return data;
}

export function downloadQueuePresetFile(preset: QueuePresetFile, filename?: string) {
  const slug = (preset.slug || preset.name)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  const blob = new Blob([JSON.stringify(preset, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || `${slug || "queue"}.queue.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
