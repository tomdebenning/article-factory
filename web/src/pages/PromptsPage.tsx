import { Navigate } from "react-router-dom";
import { DEFAULT_FLOW_PATH } from "../api";

/** Legacy route — prompts now live in flow JSON files. */
export default function PromptsPage() {
  return <Navigate to={`/flows/edit?path=${encodeURIComponent(DEFAULT_FLOW_PATH)}`} replace />;
}
