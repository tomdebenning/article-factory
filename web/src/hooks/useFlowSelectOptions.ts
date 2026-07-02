import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import {
  collectFlowFilesFromTree,
  ensureFlowSelectOption,
  FLOWS_CHANGED_EVENT,
  mergeFlowSelectLabels,
  sortFlowSelectOptions,
  type FlowSelectOption,
} from "../utils/flowSelectOptions";

export function useFlowSelectOptions(activePath?: string) {
  const location = useLocation();
  const [options, setOptions] = useState<FlowSelectOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    void api
      .getFlowTree()
      .then((tree) => {
        const optionsFromTree = sortFlowSelectOptions(collectFlowFilesFromTree(tree));
        const displayNames = new Map(
          optionsFromTree.map((option) => [
            option.path,
            option.path.replace(/\.flow\.json$/, "").split("/").pop() || option.path,
          ]),
        );
        const merged = mergeFlowSelectLabels(optionsFromTree, displayNames);
        setOptions(ensureFlowSelectOption(merged, activePath));
        setError(null);
      })
      .catch((err: Error) => {
        setError(err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [activePath]);

  useEffect(() => {
    reload();
  }, [reload, location.pathname]);

  useEffect(() => {
    const onRefresh = () => reload();
    window.addEventListener(FLOWS_CHANGED_EVENT, onRefresh);
    window.addEventListener("focus", onRefresh);
    window.addEventListener("pageshow", onRefresh);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        reload();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener(FLOWS_CHANGED_EVENT, onRefresh);
      window.removeEventListener("focus", onRefresh);
      window.removeEventListener("pageshow", onRefresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload]);

  return { options, loading, error, reload };
}
