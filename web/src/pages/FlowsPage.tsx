import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, DEFAULT_FLOW_PATH, type FlowTreeNode } from "../api";
import { downloadFlowJson, readFlowJsonFile } from "../utils/flowFiles";
import { notifyFlowsChanged } from "../utils/flowSelectOptions";
import { deskDetailUrl } from "../utils/desks";
import FlowMoveForm from "../components/FlowMoveForm";

function isTemplateFlowPath(path: string): boolean {
  return path === "_templates" || path.startsWith("_templates/");
}

type FlowListItem = {
  path: string;
  display_name: string;
  slug: string;
  step_count: number;
  modified_at?: string;
};

function TreeBranch({
  node,
  selectedPath,
  onSelect,
}: {
  node: FlowTreeNode;
  selectedPath: string;
  onSelect: (path: string, type: "folder" | "file") => void;
}) {
  const [open, setOpen] = useState(true);

  if (node.type === "file") {
    return (
      <button
        type="button"
        className={`flow-tree-item flow-tree-file${selectedPath === node.path ? " is-selected" : ""}`}
        onClick={() => onSelect(node.path, "file")}
      >
        {node.name}
      </button>
    );
  }

  return (
    <div className="flow-tree-folder">
      <button
        type="button"
        className={`flow-tree-item flow-tree-folder-head${selectedPath === node.path ? " is-selected" : ""}`}
        onClick={() => {
          setOpen((value) => !value);
          onSelect(node.path, "folder");
        }}
      >
        <span className="flow-tree-chevron">{open ? "▾" : "▸"}</span>
        {node.name || "flows"}
      </button>
      {open && node.children && node.children.length > 0 && (
        <div className="flow-tree-children">
          {node.children.map((child) => (
            <TreeBranch key={child.path || child.name} node={child} selectedPath={selectedPath} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

function flowListItemFromNode(node: FlowTreeNode): FlowListItem {
  const slug = node.name.replace(/\.flow\.json$/, "");
  return {
    path: node.path,
    display_name: slug,
    slug,
    step_count: 0,
    modified_at: node.modified_at,
  };
}

function flowsInFolder(tree: FlowTreeNode | null, folderPath: string): FlowListItem[] {
  if (!tree) return [];

  const findFolder = (node: FlowTreeNode, target: string): FlowTreeNode | null => {
    if (node.path === target) return node;
    for (const child of node.children || []) {
      if (child.type === "folder") {
        const found = findFolder(child, target);
        if (found) return found;
      }
    }
    return null;
  };

  const collectFlows = (node: FlowTreeNode, directOnly: boolean): FlowListItem[] => {
    const flows: FlowListItem[] = [];
    for (const child of node.children || []) {
      if (child.type === "file") {
        flows.push(flowListItemFromNode(child));
      } else if (!directOnly && child.type === "folder") {
        flows.push(...collectFlows(child, false));
      }
    }
    return flows;
  };

  const root = folderPath ? findFolder(tree, folderPath) : tree;
  if (!root) return [];

  return collectFlows(root, Boolean(folderPath));
}

function folderPathForSelection(selectedPath: string, selectedType: "folder" | "file"): string {
  if (selectedType === "folder") {
    return selectedPath;
  }
  const slash = selectedPath.lastIndexOf("/");
  return slash >= 0 ? selectedPath.slice(0, slash) : "";
}

export default function FlowsPage() {
  const navigate = useNavigate();
  const [tree, setTree] = useState<FlowTreeNode | null>(null);
  const [treeLoading, setTreeLoading] = useState(true);
  const [folderFlowsList, setFolderFlowsList] = useState<FlowListItem[]>([]);
  const [flowsLoading, setFlowsLoading] = useState(false);
  const [selectedPath, setSelectedPath] = useState("");
  const [selectedType, setSelectedType] = useState<"folder" | "file">("folder");
  const [folderName, setFolderName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [moveFlowPath, setMoveFlowPath] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);
  const [defaultFlowPath, setDefaultFlowPath] = useState(DEFAULT_FLOW_PATH);

  const reloadTree = () => {
    setTreeLoading(true);
    void api
      .getFlowTree()
      .then((data) => {
        setTree(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setTreeLoading(false));
  };

  const reloadFolderFlows = (folderPath: string) => {
    setFlowsLoading(true);
    void api
      .listFlows(folderPath)
      .then((data) => setFolderFlowsList(data.flows))
      .catch(() => setFolderFlowsList([]))
      .finally(() => setFlowsLoading(false));
  };

  const reload = () => {
    reloadTree();
    notifyFlowsChanged();
  };

  useEffect(() => {
    reloadTree();
    void api
      .getSettings()
      .then((settings) => {
        if (settings.default_flow_path) {
          setDefaultFlowPath(settings.default_flow_path);
        }
      })
      .catch(() => {
        /* settings optional */
      });
  }, []);

  useEffect(() => {
    if (!tree) {
      return;
    }
    reloadFolderFlows(folderPathForSelection(selectedPath, selectedType));
  }, [tree, selectedPath, selectedType]);

  const folderFlowsFromTree = useMemo(
    () => flowsInFolder(tree, folderPathForSelection(selectedPath, selectedType)),
    [tree, selectedPath, selectedType],
  );

  const folderFlows = folderFlowsList.length > 0 ? folderFlowsList : folderFlowsFromTree;

  const createFolder = () => {
    const name = folderName.trim();
    if (!name) return;
    const path = selectedType === "folder" && selectedPath ? `${selectedPath}/${name}` : name;
    setError(null);
    void api
      .createFlowFolder(path)
      .then(() => {
        setFolderName("");
        setMessage(`Created folder ${path}`);
        reload();
      })
      .catch((e: Error) => setError(e.message));
  };

  const deleteFolder = () => {
    if (!selectedPath || selectedType !== "folder") return;
    if (!window.confirm(`Delete empty folder ${selectedPath}?`)) return;
    setError(null);
    void api
      .deleteFlowFolder(selectedPath)
      .then(() => {
        setMessage(`Deleted folder ${selectedPath}`);
        setSelectedPath("");
        reload();
      })
      .catch((e: Error) => setError(e.message));
  };

  const duplicateFlow = (path: string) => {
    setBusyPath(path);
    setError(null);
    void api
      .duplicateFlow(path)
      .then((result) => {
        setMessage(`Duplicated to ${result.path}`);
        reload();
        navigate(`/flows/edit?path=${encodeURIComponent(result.path)}`);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusyPath(null));
  };

  const deleteFlow = (path: string) => {
    if (!window.confirm(`Delete desk ${path}?`)) return;
    setBusyPath(path);
    setError(null);
    void api
      .deleteFlow(path)
      .then(() => {
        setMessage(`Deleted ${path}`);
        if (selectedPath === path) setSelectedPath("");
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusyPath(null));
  };

  const exportFlow = (path: string) => {
    setError(null);
    void api
      .exportFlow(path)
      .then((data) => {
        downloadFlowJson(data.path, data.flow);
        setMessage(`Exported ${path}`);
      })
      .catch((e: Error) => setError(e.message));
  };

  const importFlowFile = async (file: File) => {
    setError(null);
    try {
      const flow = await readFlowJsonFile(file);
      const slug = flow.slug || file.name.replace(/\.flow\.json$/, "").replace(/\.json$/, "");
      const folder =
        selectedType === "folder" && selectedPath && !selectedPath.startsWith("_templates")
          ? selectedPath
          : "";
      const result = await api.importFlow({ folder, slug, flow });
      setMessage(`Imported ${result.path}`);
      reload();
      navigate(`/flows/edit?path=${encodeURIComponent(result.path)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not import desk file.");
    }
  };

  return (
    <section className="card flows-page">
      <div className="flows-page-head">
        <h2>Desks</h2>
        <div className="flow-page-actions">
          <button type="button" className="secondary" onClick={() => importInputRef.current?.click()}>
            Import JSON
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void importFlowFile(file);
            }}
          />
          <Link to="/flows/new" className="primary flows-new-link">
            Create desk
          </Link>
        </div>
      </div>
      <p className="hint">
        Desks define the editorial process for a beat — reporter, editor, and other steps. Each assignment
        runs through one desk. Edit steps, loops, and prompts here.
      </p>
      <div className="flow-default-banner">
        <span>Default desk:</span>
        <code>{defaultFlowPath}</code>
        <Link to="/settings">Change in settings</Link>
        <Link to={`/flows/edit?path=${encodeURIComponent(defaultFlowPath)}`}>Open prompts</Link>
      </div>
      {error && <p className="error">{error}</p>}
      {message && <p className="ok">{message}</p>}

      <div className="flows-layout">
        <aside className="flow-tree-panel">
          <h3>Browse</h3>
          {treeLoading && !tree && <p className="hint">Loading folders…</p>}
          {tree && (
            <div className="flow-tree">
              <TreeBranch
                node={tree}
                selectedPath={selectedPath}
                onSelect={(path, type) => {
                  setSelectedPath(path);
                  setSelectedType(type);
                  if (type === "file") {
                    navigate(`/flows/edit?path=${encodeURIComponent(path)}`);
                  }
                }}
              />
            </div>
          )}
          <div className="flow-folder-create">
            <input
              type="text"
              placeholder="New folder name"
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
            />
            <button type="button" className="secondary" onClick={createFolder}>
              Add folder
            </button>
            {selectedType === "folder" && selectedPath && (
              <button type="button" className="secondary run-delete-button" onClick={deleteFolder}>
                Delete empty folder
              </button>
            )}
          </div>
        </aside>

        <div className="flow-tree-detail">
          <h3>{selectedType === "file" && selectedPath ? selectedPath : selectedPath || "All desks"}</h3>
          {selectedType === "folder" && selectedPath === "_templates" && (
            <p className="hint flow-template-note">
              Templates in <code>_templates</code> are starting points only. Move one into a regular folder to use it on{" "}
              <Link to="/start-flows">Plan a shift</Link>.
            </p>
          )}
          {flowsLoading && folderFlows.length === 0 && <p className="hint">Loading desks…</p>}
          {!flowsLoading && folderFlows.length === 0 && (
            <p className="hint">No desks here yet. <Link to="/flows/new">Create one</Link>.</p>
          )}
          {folderFlows.length > 0 && (
            <ul className="flow-file-list">
              {folderFlows.map((flow) => (
                <li key={flow.path} className="flow-file-list-item">
                  <div className="flow-file-list-main">
                    <Link to={deskDetailUrl(flow.path)}>
                      <strong>{flow.display_name}</strong>
                    </Link>
                    <span className="hint">{flow.path}</span>
                    <span className="hint">
                      {flow.step_count > 0 ? `${flow.step_count} steps` : "flow file"}
                      {flow.modified_at ? ` · ${new Date(flow.modified_at).toLocaleString()}` : ""}
                    </span>
                  </div>
                  <div className="flow-file-list-actions">
                    <Link to={deskDetailUrl(flow.path)} className="secondary">
                      Open desk
                    </Link>
                    <Link to={`/flows/edit?path=${encodeURIComponent(flow.path)}`} className="secondary">
                      Edit pipeline
                    </Link>
                    <Link
                      to={`/flows/performance?path=${encodeURIComponent(flow.path)}`}
                      className="secondary"
                    >
                      Performance
                    </Link>
                    {isTemplateFlowPath(flow.path) && (
                      <button
                        type="button"
                        className="secondary"
                        disabled={busyPath === flow.path}
                        onClick={() => setMoveFlowPath(moveFlowPath === flow.path ? null : flow.path)}
                      >
                        {moveFlowPath === flow.path ? "Cancel move" : "Move to library"}
                      </button>
                    )}
                    <button
                      type="button"
                      className="secondary"
                      disabled={busyPath === flow.path}
                      onClick={() => exportFlow(flow.path)}
                    >
                      Export
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busyPath === flow.path}
                      onClick={() => duplicateFlow(flow.path)}
                    >
                      Duplicate
                    </button>
                    <button
                      type="button"
                      className="secondary run-delete-button"
                      disabled={busyPath === flow.path}
                      onClick={() => deleteFlow(flow.path)}
                    >
                      Delete
                    </button>
                  </div>
                  {moveFlowPath === flow.path && (
                    <FlowMoveForm
                      flowPath={flow.path}
                      defaultSlug={flow.slug}
                      busy={busyPath === flow.path}
                      onBusyChange={(busy) => setBusyPath(busy ? flow.path : null)}
                      onError={setError}
                      onMoved={(newPath) => {
                        setMoveFlowPath(null);
                        setMessage(`Moved to ${newPath}`);
                        reload();
                        notifyFlowsChanged();
                        navigate(`/flows/edit?path=${encodeURIComponent(newPath)}`, {
                          state: { flow_path: newPath },
                        });
                      }}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
