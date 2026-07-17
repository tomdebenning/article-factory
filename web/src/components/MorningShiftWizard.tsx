import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { FactoryStatus } from "../api";

const DISMISS_KEY = "newsroom_morning_shift_onboarding_dismissed";

type Step = NonNullable<FactoryStatus["onboarding"]>["steps"][number];

type Props = {
  onboarding: NonNullable<FactoryStatus["onboarding"]>;
  setupComplete: boolean;
};

export default function MorningShiftWizard({ onboarding, setupComplete }: Props) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(DISMISS_KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);

  if (!onboarding.show_wizard || dismissed || onboarding.completed) {
    return null;
  }

  const steps: Step[] = onboarding.steps.map((step) => {
    if (step.id === "settings") {
      return { ...step, ok: setupComplete };
    }
    return step;
  });

  const dismiss = () => {
    try {
      window.localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
    setDismissed(true);
  };

  return (
    <section className="card morning-shift-wizard">
      <div className="morning-shift-wizard-head">
        <div>
          <p className="home-eyebrow">Getting started</p>
          <h3>Run your first Morning Shift</h3>
          <p className="hint">
            Walk through setup once — then the scheduler can staff and activate shifts around the clock.
          </p>
        </div>
        <button type="button" className="secondary" onClick={dismiss}>
          Dismiss
        </button>
      </div>
      <ol className="morning-shift-steps">
        {steps.map((step) => (
          <li key={step.id} className={step.ok ? "is-done" : ""}>
            <span className="morning-shift-step-icon">{step.ok ? "✓" : "○"}</span>
            <div>
              <strong>{step.label}</strong>
              <p className="hint">{step.description}</p>
              {!step.ok && (
                <Link to={step.action_path} className="morning-shift-step-link">
                  Continue →
                </Link>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
