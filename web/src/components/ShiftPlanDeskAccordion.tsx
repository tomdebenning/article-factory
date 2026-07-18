import { useState } from "react";
import { Link } from "react-router-dom";
import TopicListEditor from "./TopicListEditor";
import { deskDetailUrl } from "../utils/desks";
import {
  deskAssignmentCount,
  deskFillLabel,
  deskFillLevel,
  type DeskFillLevel,
} from "../utils/shiftPlanDesk";

export type ShiftPlanDeskDraft = {
  desk_path: string;
  display_name: string;
  topic_slug: string;
  topics: string[];
  reporter_selection_mode: "round_robin" | "lru";
};

type Props = {
  desk: ShiftPlanDeskDraft;
  shiftLabel: string;
  disabled?: boolean;
  onChange: (patch: Partial<ShiftPlanDeskDraft>) => void;
  onGenerate: (count: number) => Promise<void>;
  onLoadStanding: () => Promise<void>;
};

export default function ShiftPlanDeskAccordion({
  desk,
  shiftLabel,
  disabled = false,
  onChange,
  onGenerate,
  onLoadStanding,
}: Props) {
  const [topicCount, setTopicCount] = useState("3");
  const [generating, setGenerating] = useState(false);
  const [loadingStanding, setLoadingStanding] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const count = deskAssignmentCount(desk.topics);
  const level: DeskFillLevel = deskFillLevel(desk.topics);
  const busy = disabled || generating || loadingStanding;

  const runGenerate = () => {
    const parsed = Number(topicCount);
    if (!Number.isFinite(parsed) || parsed < 1 || parsed > 20) {
      setLocalError("Topic count must be between 1 and 20.");
      return;
    }
    setLocalError(null);
    setGenerating(true);
    void onGenerate(parsed)
      .catch((e: Error) => setLocalError(e.message))
      .finally(() => setGenerating(false));
  };

  const runLoadStanding = () => {
    setLocalError(null);
    setLoadingStanding(true);
    void onLoadStanding()
      .catch((e: Error) => setLocalError(e.message))
      .finally(() => setLoadingStanding(false));
  };

  return (
    <details className={`shift-plan-desk shift-plan-desk--${level}`}>
      <summary className="shift-plan-desk-summary">
        <span className={`shift-plan-desk-status shift-plan-desk-status--${level}`} aria-hidden />
        <span className="shift-plan-desk-title">
          <strong>{desk.display_name}</strong>
          <span className="hint shift-plan-desk-path">{desk.desk_path}</span>
        </span>
        <span className="shift-plan-desk-meta">{deskFillLabel(level, count)}</span>
      </summary>

      <div className="shift-plan-desk-body">
        {localError && <p className="error">{localError}</p>}

        <div className="shift-plan-desk-toolbar">
          <label>
            Generate count
            <input
              type="number"
              min={1}
              max={20}
              value={topicCount}
              disabled={busy}
              onChange={(e) => setTopicCount(e.target.value)}
            />
          </label>
          <div className="desk-page-actions">
            <button type="button" className="primary" disabled={busy} onClick={runGenerate}>
              {generating ? "Generating…" : `Generate for ${shiftLabel}`}
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={runLoadStanding}>
              {loadingStanding ? "Loading…" : "Load standing order"}
            </button>
            <Link to={deskDetailUrl(desk.desk_path, { tab: "queue" })} className="secondary">
              Open desk queue
            </Link>
          </div>
        </div>

        <div className="start-flows-composer-grid shift-plan-desk-fields">
          <label>
            Edition topic
            <input
              value={desk.topic_slug}
              disabled={busy}
              onChange={(e) => onChange({ topic_slug: e.target.value })}
              placeholder="sports"
            />
          </label>
          <label>
            Reporter selection
            <select
              value={desk.reporter_selection_mode}
              disabled={busy}
              onChange={(e) =>
                onChange({
                  reporter_selection_mode: e.target.value === "lru" ? "lru" : "round_robin",
                })
              }
            >
              <option value="round_robin">Round robin from desk pool</option>
              <option value="lru">Least recently used from desk pool</option>
            </select>
          </label>
        </div>

        <TopicListEditor
          topics={desk.topics}
          onChange={(topics) => onChange({ topics })}
          disabled={busy}
          label={`${shiftLabel} assignments`}
          countLabel="assignment"
          emptyLabel="No assignments yet — generate topics or add manually."
        />
      </div>
    </details>
  );
}
