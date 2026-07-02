import { useState } from "react";

type Props = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoComplete?: string;
};

export default function SecretInput({ value, onChange, placeholder, autoComplete }: Props) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="secret-input-wrap">
      <input
        type={visible ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
      />
      <button
        type="button"
        className="secondary secret-input-toggle"
        onClick={() => setVisible((show) => !show)}
        aria-pressed={visible}
        aria-label={visible ? "Hide value" : "Show value"}
      >
        {visible ? "Hide" : "Show"}
      </button>
    </div>
  );
}
