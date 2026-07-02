import { useRef, useState } from "react";
import { parseTopicsFromFile, parseTopicsFromText } from "../utils/parseTopicFile";

function mergeTopics(existing: string[], incoming: string[]): string[] {
  const seen = new Set(existing);
  const merged = [...existing];
  for (const topic of incoming) {
    const trimmed = topic.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    merged.push(trimmed);
  }
  return merged;
}

export type TopicListEditorProps = {
  topics: string[];
  onChange: (topics: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
  label?: string;
  showUpload?: boolean;
};

export default function TopicListEditor({
  topics,
  onChange,
  disabled = false,
  placeholder = "An article about Oklahoma State Football",
  label = "Topics",
  showUpload = true,
}: TopicListEditorProps) {
  const [draft, setDraft] = useState("");
  const [uploadLabel, setUploadLabel] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const addDraftTopics = () => {
    const parsed = parseTopicsFromText(draft, "topics.txt");
    if (parsed.length === 0) return;
    onChange(mergeTopics(topics, parsed));
    setDraft("");
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData.getData("text");
    if (!pasted.includes("\n")) return;
    event.preventDefault();
    const parsed = parseTopicsFromText(pasted, "topics.txt");
    if (parsed.length > 0) {
      onChange(mergeTopics(topics, parsed));
      setDraft("");
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addDraftTopics();
    }
  };

  const handleFile = (file: File | null) => {
    if (!file || disabled) return;
    void parseTopicsFromFile(file)
      .then((parsed) => {
        if (parsed.length === 0) return;
        onChange(mergeTopics(topics, parsed));
        setUploadLabel(`${file.name} · +${parsed.length}`);
      })
      .catch(() => {
        setUploadLabel(null);
      });
  };

  const removeTopic = (index: number) => {
    onChange(topics.filter((_, i) => i !== index));
  };

  const clearAll = () => {
    onChange([]);
    setUploadLabel(null);
  };

  return (
    <div className={`topic-list-editor${disabled ? " is-disabled" : ""}`}>
      <div className="topic-list-editor-header">
        <span className="topic-list-editor-label">{label}</span>
        <span className="topic-list-editor-count">
          {topics.length} {topics.length === 1 ? "topic" : "topics"}
        </span>
      </div>

      <div className="topic-list-add-row">
        <input
          type="text"
          value={draft}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
        />
        <button type="button" className="secondary" disabled={disabled || !draft.trim()} onClick={addDraftTopics}>
          Add
        </button>
      </div>

      {showUpload && (
        <div className="topic-list-upload-row">
          <label className={`file-upload-button${disabled ? " is-disabled" : ""}`}>
            Upload from file
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.csv,text/plain,text/csv"
              hidden
              disabled={disabled}
              onChange={(e) => {
                handleFile(e.target.files?.[0] ?? null);
                e.target.value = "";
              }}
            />
          </label>
          <span className="hint">.txt = one per line · .csv = first column</span>
          {uploadLabel && <span className="hint topic-list-upload-note">{uploadLabel}</span>}
        </div>
      )}

      {topics.length === 0 ? (
        <p className="topic-list-empty hint">No topics yet. Add one above or upload a file.</p>
      ) : (
        <ol className="topic-list-items">
          {topics.map((topic, index) => (
            <li key={`${index}-${topic.slice(0, 24)}`} className="topic-list-item">
              <span className="topic-list-index">{index + 1}</span>
              <span className="topic-list-text">{topic}</span>
              <button
                type="button"
                className="topic-list-remove"
                disabled={disabled}
                aria-label={`Remove topic ${index + 1}`}
                onClick={() => removeTopic(index)}
              >
                ×
              </button>
            </li>
          ))}
        </ol>
      )}

      {topics.length > 0 && (
        <button type="button" className="secondary topic-list-clear" disabled={disabled} onClick={clearAll}>
          Clear all topics
        </button>
      )}
    </div>
  );
}

export { mergeTopics };
