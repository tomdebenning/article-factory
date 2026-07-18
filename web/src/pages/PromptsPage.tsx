import { Navigate } from "react-router-dom";
import { DEFAULT_FLOW_PATH } from "../api";

/** Legacy route — Edition topic slugs are not prompt stores. */
export default function PromptsPage() {
  return <Navigate to={`/desks?path=${encodeURIComponent(DEFAULT_FLOW_PATH)}`} replace />;
}
