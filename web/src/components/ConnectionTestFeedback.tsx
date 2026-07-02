export type ConnectionTestState = {
  status: "idle" | "testing" | "success" | "error";
  message: string;
};

type Props = {
  state: ConnectionTestState;
  successTitle?: string;
  errorTitle?: string;
};

export default function ConnectionTestFeedback({
  state,
  successTitle = "Connection OK",
  errorTitle = "Connection failed",
}: Props) {
  if (state.status === "idle") {
    return null;
  }

  if (state.status === "testing") {
    return (
      <div className="connection-test-result testing" role="status" aria-live="polite">
        Testing connection…
      </div>
    );
  }

  const isSuccess = state.status === "success";
  return (
    <div
      className={`connection-test-result ${isSuccess ? "success" : "error"}`}
      role="status"
      aria-live="polite"
    >
      <strong>{isSuccess ? successTitle : errorTitle}</strong>
      <p>{state.message}</p>
    </div>
  );
}
