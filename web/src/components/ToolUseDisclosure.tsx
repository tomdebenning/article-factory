import type { ToolUseEntry } from "../api";

export default function ToolUseDisclosure({
  tools,
  live = false,
}: {
  tools: ToolUseEntry[];
  live?: boolean;
}) {
  if (!tools.length) {
    return null;
  }

  return (
    <details className="tool-use-disclosure" open={live || tools.length > 0 || undefined}>
      <summary>{live ? `Tool use in progress (${tools.length})` : `Tool use (${tools.length})`}</summary>
      <ul className="tool-use-list">
        {tools.map((entry, index) => (
          <li key={`${entry.tool}-${entry.round ?? 0}-${index}`} className={entry.ok === false ? "tool-use-failed" : undefined}>
            <strong>{entry.label || entry.tool}</strong>
            {entry.detail ? <span className="tool-use-detail">{entry.detail}</span> : null}
            {entry.round != null && entry.round > 0 ? (
              <span className="tool-use-round">round {entry.round}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}
