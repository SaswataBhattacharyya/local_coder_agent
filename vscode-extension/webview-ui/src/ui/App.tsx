import React, { useEffect, useMemo, useState } from "react";
import { parseUnifiedDiff, buildUnifiedDiff, DiffFile, DiffLine } from "./diff";

type ChatMessage = { role: string; text: string; timestamp: number; streaming?: boolean };
type PendingPatch = { diff: string; summary: string; riskNotes: string };

type AgentState = {
  status: string;
  serverUrl: string;
  messages: ChatMessage[];
  pending: PendingPatch | null;
  modelStatusText: string;
  modelInfo?: any;
  mcpStatusText: string;
  snapshotsText: string;
  ingestStatusText: string;
  progress?: { text: string; status: "running" | "done" | "error" }[];
  indexStatus?: {
    in_progress?: boolean;
    last_run_ts?: number;
    last_duration_ms?: number;
    last_error?: string;
    freshness?: string;
  };
  repoRoot?: string;
  repoRootStateless?: boolean;
  repoRootRequested?: string;
  inferenceConfig?: any;
};

type ImagePreview = { name: string; data: string };

type Panel = "none" | "settings" | "mcp" | "history";

const vscode = (window as any).acquireVsCodeApi?.();

export const App: React.FC = () => {
  const [panel, setPanel] = useState<Panel>("none");
  const [state, setState] = useState<AgentState>({
    status: "Not connected",
    serverUrl: "",
    messages: [],
    pending: null,
    modelStatusText: "",
    modelInfo: null,
    mcpStatusText: "",
    snapshotsText: "",
    ingestStatusText: "",
    progress: [],
  });
  const [images, setImages] = useState<ImagePreview[]>([]);
  const [showAddModel, setShowAddModel] = useState(false);
  const [showRemoveModel, setShowRemoveModel] = useState(false);
  const [removeRole, setRemoveRole] = useState("reasoner");
  const [removeIds, setRemoveIds] = useState<string[]>([]);
  const [modelForm, setModelForm] = useState({
    role: "reasoner",
    model_id: "",
    repo_id: "",
    filename_hint: "Q4_K_M",
    download_now: true,
  });
  const [inferenceForm, setInferenceForm] = useState<any>({
    mode: "local",
    roles: {
      reasoner: { backend: "local", remote_url: "", model: "", api_key: "" },
      coder: { backend: "local", remote_url: "", model: "", api_key: "" },
      vlm: { backend: "local", remote_url: "", model: "", api_key: "" },
    },
  });

  const diffFiles = useMemo<DiffFile[]>(() => {
    if (!state.pending?.diff) return [];
    return parseUnifiedDiff(state.pending.diff);
  }, [state.pending?.diff]);

  const [files, setFiles] = useState<DiffFile[]>([]);

  useEffect(() => {
    setFiles(diffFiles);
  }, [diffFiles]);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const msg = event.data;
      if (msg?.type === "state") {
        setState((prev) => ({ ...prev, ...msg }));
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  useEffect(() => {
    if (state.inferenceConfig) {
      setInferenceForm(state.inferenceConfig);
    }
  }, [state.inferenceConfig]);

  const send = () => {
    const input = document.getElementById("input") as HTMLTextAreaElement | null;
    const text = input?.value?.trim() || "";
    if (!text && images.length === 0) return;
    vscode?.postMessage({ type: "send", text, images });
    if (input) input.value = "";
    setImages([]);
  };

  const interrupt = () => {
    vscode?.postMessage({ type: "action", action: "interrupt" });
  };

  const runAction = (action: string, payload: any = {}) => {
    vscode?.postMessage({ type: "action", action, ...payload });
  };

  const approveAll = () => {
    setFiles((prev) =>
      prev.map((f) => ({
        ...f,
        hunks: f.hunks.map((h) => ({
          ...h,
          lines: h.lines.map((l) =>
            l.type === "add" || l.type === "del" ? { ...l, accepted: true } : l,
          ),
        })),
      })),
    );
  };

  const rejectAll = () => {
    setFiles((prev) =>
      prev.map((f) => ({
        ...f,
        hunks: f.hunks.map((h) => ({
          ...h,
          lines: h.lines.map((l) =>
            l.type === "add" || l.type === "del" ? { ...l, accepted: false } : l,
          ),
        })),
      })),
    );
  };

  const toggleLine = (line: DiffLine, accept: boolean) => {
    if (!(line.type === "add" || line.type === "del")) return;
    line.accepted = accept;
    setFiles((prev) => [...prev]);
  };

  const approveSelected = () => {
    const diff = buildUnifiedDiff(files);
    runAction("approve", { diff });
  };

  const isStreaming = state.messages.some((m) => m.streaming);

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items || [];
    const next: ImagePreview[] = [];
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (!file) continue;
        const reader = new FileReader();
        reader.onload = () => {
          next.push({ name: file.name || "pasted-image", data: String(reader.result || "") });
          setImages((prev) => [...prev, ...next]);
        };
        reader.readAsDataURL(file);
      }
    }
  };

  const onDrop = (e: React.DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files || []);
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      const reader = new FileReader();
      reader.onload = () => {
        setImages((prev) => [...prev, { name: file.name, data: String(reader.result || "") }]);
      };
      reader.readAsDataURL(file);
    }
  };

  const onDragOver = (e: React.DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
  };

  const removeImage = (idx: number) => {
    setImages((prev) => prev.filter((_, i) => i !== idx));
  };

  const renderMarkdown = (text: string) => {
    const escape = (s: string) =>
      s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    const blocks = text.split(/```/);
    const out: React.ReactNode[] = [];
    for (let i = 0; i < blocks.length; i++) {
      if (i % 2 === 1) {
        out.push(
          <pre key={`code-${i}`} className="codeblock">
            <code>{blocks[i]}</code>
          </pre>,
        );
        continue;
      }
      const lines = blocks[i].split("\n");
      const parts: React.ReactNode[] = [];
      let list: string[] = [];
      const flushList = () => {
        if (list.length === 0) return;
        parts.push(
          <ul key={`list-${i}-${parts.length}`}>
            {list.map((l, idx) => (
              <li key={idx}>{l}</li>
            ))}
          </ul>,
        );
        list = [];
      };
      for (const line of lines) {
        if (line.startsWith("- ")) {
          list.push(line.slice(2));
          continue;
        }
        flushList();
        if (line.startsWith("# ")) {
          parts.push(<h3 key={`h-${i}-${parts.length}`}>{line.slice(2)}</h3>);
        } else if (line.trim() === "") {
          parts.push(<div key={`br-${i}-${parts.length}`} className="spacer" />);
        } else {
          const html = escape(line).replace(/`([^`]+)`/g, "<code>$1</code>");
          parts.push(
            <div key={`t-${i}-${parts.length}`} dangerouslySetInnerHTML={{ __html: html }} />,
          );
        }
      }
      flushList();
      out.push(<div key={`blk-${i}`}>{parts}</div>);
    }
    return out;
  };

  const parsedMcp = (() => {
    try {
      return JSON.parse(state.mcpStatusText || "{}");
    } catch {
      return null;
    }
  })();

  const parsedSnapshots = (() => {
    try {
      return JSON.parse(state.snapshotsText || "{}");
    } catch {
      return null;
    }
  })();

  const indexLabel = state.indexStatus?.in_progress
    ? "Indexing"
    : state.indexStatus?.freshness || "unknown";
  const indexClass = state.indexStatus?.in_progress
    ? "running"
    : state.indexStatus?.freshness === "stale"
    ? "stale"
    : "fresh";

  return (
    <div className="app">
      <div className="topbar">
        <div className="status">Status: {state.status}</div>
        <div className="status">Server: {state.serverUrl}</div>
        <div className={`index-tag ${indexClass}`}>Index: {indexLabel}</div>
        <div className="controls">
          <button onClick={() => runAction("ping")}>Ping</button>
          <button onClick={() => setPanel(panel === "settings" ? "none" : "settings")}>Settings</button>
          <button
            onClick={() => {
              setPanel(panel === "mcp" ? "none" : "mcp");
              runAction("mcpOpenConfig");
            }}
          >
            MCP
          </button>
          <button onClick={() => setPanel(panel === "history" ? "none" : "history")}>History</button>
        </div>
      </div>

      {panel !== "none" && (
        <div className="panel-area">
          {panel === "settings" && (
            <div className="panel">
              <h3>Models</h3>
              <button onClick={() => setShowAddModel(true)}>Add Model</button>
              <button onClick={() => setShowRemoveModel(true)}>Remove Models</button>
              <div className="field">
                <label>Reasoning Model</label>
                <select
                  value={state.modelInfo?.reasoner?.selected === "best" ? (state.modelInfo?.reasoner?.default || "") : (state.modelInfo?.reasoner?.selected || "")}
                  onChange={(e) => vscode?.postMessage({ type: "selectModel", role: "reasoner", modelId: e.target.value })}
                >
                  {(state.modelInfo?.reasoner?.options || []).filter((o: any) => o.id !== "best").map((opt: any) => (
                    <option key={opt.id} value={opt.id}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>Coding Model</label>
                <select
                  value={state.modelInfo?.coder?.selected === "best" ? (state.modelInfo?.coder?.default || "") : (state.modelInfo?.coder?.selected || "")}
                  onChange={(e) => vscode?.postMessage({ type: "selectModel", role: "coder", modelId: e.target.value })}
                >
                  {(state.modelInfo?.coder?.options || []).filter((o: any) => o.id !== "best").map((opt: any) => (
                    <option key={opt.id} value={opt.id}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>Image Model</label>
                <select
                  value={state.modelInfo?.vlm?.selected === "best" ? (state.modelInfo?.vlm?.default || "") : (state.modelInfo?.vlm?.selected || "")}
                  onChange={(e) => vscode?.postMessage({ type: "selectModel", role: "vlm", modelId: e.target.value })}
                >
                  {(state.modelInfo?.vlm?.options || []).filter((o: any) => o.id !== "best").map((opt: any) => (
                    <option key={opt.id} value={opt.id}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <h3>Plugins</h3>
              <div className="field">
                <label>Plugins</label>
                <select disabled>
                  <option>No plugins installed</option>
                </select>
              </div>
              <h3>Inference</h3>
              <div className="field">
                <label>Mode</label>
                <select
                  value={inferenceForm.mode || "local"}
                  onChange={(e) => setInferenceForm({ ...inferenceForm, mode: e.target.value })}
                >
                  <option value="local">Local</option>
                  <option value="remote">Remote</option>
                  <option value="mixed">Mixed</option>
                </select>
              </div>
              {(["reasoner", "coder", "vlm"] as const).map((role) => (
                <div key={role} className="inference-role">
                  <div className="muted">{role.toUpperCase()}</div>
                  <div className="field">
                    <label>Backend</label>
                    <select
                      value={inferenceForm.roles?.[role]?.backend || "local"}
                      onChange={(e) =>
                        setInferenceForm({
                          ...inferenceForm,
                          roles: {
                            ...inferenceForm.roles,
                            [role]: { ...inferenceForm.roles?.[role], backend: e.target.value },
                          },
                        })
                      }
                    >
                      <option value="local">Local</option>
                      <option value="remote">Remote</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>Remote URL</label>
                    <input
                      value={inferenceForm.roles?.[role]?.remote_url || ""}
                      onChange={(e) =>
                        setInferenceForm({
                          ...inferenceForm,
                          roles: {
                            ...inferenceForm.roles,
                            [role]: { ...inferenceForm.roles?.[role], remote_url: e.target.value },
                          },
                        })
                      }
                      placeholder="http://127.0.0.1:18080/v1/chat/completions"
                    />
                  </div>
                  <div className="field">
                    <label>Model</label>
                    <input
                      value={inferenceForm.roles?.[role]?.model || ""}
                      onChange={(e) =>
                        setInferenceForm({
                          ...inferenceForm,
                          roles: {
                            ...inferenceForm.roles,
                            [role]: { ...inferenceForm.roles?.[role], model: e.target.value },
                          },
                        })
                      }
                      placeholder="model name"
                    />
                  </div>
                  <div className="field">
                    <label>API Key (optional)</label>
                    <input
                      value={inferenceForm.roles?.[role]?.api_key || ""}
                      onChange={(e) =>
                        setInferenceForm({
                          ...inferenceForm,
                          roles: {
                            ...inferenceForm.roles,
                            [role]: { ...inferenceForm.roles?.[role], api_key: e.target.value },
                          },
                        })
                      }
                      placeholder="leave blank if not needed"
                    />
                  </div>
                </div>
              ))}
              <div className="row">
                <button
                  onClick={() => {
                    vscode?.postMessage({ type: "action", action: "saveInference", ...inferenceForm });
                  }}
                >
                  Save Inference
                </button>
              </div>
            </div>
          )}

          {panel === "mcp" && (
            <div className="panel">
              <h3>MCP</h3>
              {!parsedMcp && <div className="muted">No MCP status available.</div>}
              {parsedMcp && (
                <div className="list">
                  {(parsedMcp.servers || []).map((s: string) => (
                    <div key={s} className="list-row">
                      <div>{s}</div>
                      <div className="tag">{parsedMcp.mcp_allowed ? "Usable" : "Disabled"}</div>
                    </div>
                  ))}
                  {parsedMcp.repo_root && (
                    <div className="muted">Repo: {parsedMcp.repo_root}</div>
                  )}
                </div>
              )}
            </div>
          )}

          {panel === "history" && (
            <div className="panel">
              <h3>Snapshots</h3>
              {parsedSnapshots?.snapshots?.length ? (
                <div className="list">
                  {parsedSnapshots.snapshots.map((s: any) => (
                    <div key={s.snapshot_id} className="list-row">
                      <div>
                        <div className="strong">{s.snapshot_id}</div>
                        <div className="muted">{new Date((s.created_at || 0) * 1000).toLocaleString()}</div>
                      </div>
                      <button onClick={() => runAction("snapshotRestore", { id: s.snapshot_id })}>Restore</button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="muted">No snapshots yet.</div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="chat-area">
        {state.repoRootStateless && (
          <div className="banner warning">
            Workspace path not visible in VM. Mount/sync required. Current repo_root is stateless.
            {state.repoRootRequested && <div className="muted">Requested: {state.repoRootRequested}</div>}
            {state.repoRoot && <div className="muted">Current: {state.repoRoot}</div>}
          </div>
        )}
        {state.progress && state.progress.length > 0 && (
          <div className="system-messages">
            {state.progress.map((p, i) => (
              <div key={i} className={`system-line ${p.status}`}>{p.text}</div>
            ))}
          </div>
        )}
        <div className="messages">
          {state.messages.map((m, idx) => (
            <div key={idx} className={`msg ${m.role}`}>
              {m.role === "assistant" ? renderMarkdown(m.text) : <div>{m.text}</div>}
              {m.streaming && <span className="streaming-dots" />}
            </div>
          ))}
        </div>
      </div>

      {state.pending?.diff && (
        <div className="patch-actions">
          <button onClick={approveSelected}>Approve Changes</button>
          <button onClick={() => runAction("reject")}>Reject Changes</button>
        </div>
      )}

      <div className="input-area">
        <div className="input-wrap">
          <textarea
            id="input"
            placeholder="Describe your request..."
            onPaste={onPaste}
            onDrop={onDrop}
            onDragOver={onDragOver}
          />
          <button
            className={`send-btn ${isStreaming ? "busy" : ""}`}
            onClick={isStreaming ? interrupt : send}
            title={isStreaming ? "Interrupt" : "Send"}
          >
            {isStreaming ? "⏸" : "▶"}
          </button>
        </div>
        {images.length > 0 && (
          <div className="image-strip">
            {images.map((img, i) => (
              <div key={i} className="thumb">
                <img src={img.data} alt={img.name} />
                <button onClick={() => removeImage(i)}>×</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {state.pending?.diff && (
        <div className="diff-view">
          <div className="row">
            <button onClick={approveAll}>Approve All</button>
            <button onClick={rejectAll}>Reject All</button>
          </div>
          {files.map((f, i) => (
            <div key={i} className="file">
              <div className="file-title">{f.path || "file"}</div>
              {f.hunks.map((h, j) => (
                <div key={j} className="hunk">
                  <div className="hunk-header">{h.header}</div>
                  {h.lines.map((l, k) => (
                    <div key={k} className={`line ${l.type}`}>
                      <span className="ln">{l.oldLine ?? ""}</span>
                      <span className="ln">{l.newLine ?? ""}</span>
                      <span className="text">{l.text}</span>
                      {(l.type === "add" || l.type === "del") && (
                        <span className="hover-actions">
                          <button onClick={() => toggleLine(l, true)}>Approve</button>
                          <button onClick={() => toggleLine(l, false)}>Reject</button>
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {showAddModel && (
        <div className="modal">
          <div className="modal-card">
            <h3>Add Model</h3>
            <div className="field">
              <label>Type</label>
              <select
                value={modelForm.role}
                onChange={(e) => setModelForm({ ...modelForm, role: e.target.value })}
              >
                <option value="reasoner">Reasoning</option>
                <option value="coder">Coder</option>
                <option value="vlm">Image</option>
              </select>
            </div>
            <div className="field">
              <label>Model Name</label>
              <input
                value={modelForm.model_id}
                onChange={(e) => setModelForm({ ...modelForm, model_id: e.target.value })}
                placeholder="e.g. my-qwen-model"
              />
            </div>
            <div className="field">
              <label>Model Link (HuggingFace repo)</label>
              <input
                value={modelForm.repo_id}
                onChange={(e) => setModelForm({ ...modelForm, repo_id: e.target.value })}
                placeholder="org/model-repo"
              />
            </div>
            <div className="field">
              <label>Filename Hint</label>
              <input
                value={modelForm.filename_hint}
                onChange={(e) => setModelForm({ ...modelForm, filename_hint: e.target.value })}
                placeholder="Q4_K_M"
              />
            </div>
            <div className="field">
              <label>Download</label>
              <select
                value={modelForm.download_now ? "now" : "later"}
                onChange={(e) => setModelForm({ ...modelForm, download_now: e.target.value === "now" })}
              >
                <option value="now">Download now</option>
                <option value="later">Download later</option>
              </select>
            </div>
            <div className="row">
              <button onClick={() => setShowAddModel(false)}>Cancel</button>
              <button
                onClick={() => {
                  vscode?.postMessage({ type: "action", action: "addModel", ...modelForm });
                  setShowAddModel(false);
                }}
              >
                Add
              </button>
            </div>
          </div>
        </div>
      )}

      {showRemoveModel && (
        <div className="modal modal-remove">
          <div className="modal-card">
            <h3>Remove Models</h3>
            <div className="field">
              <label>Type</label>
              <select value={removeRole} onChange={(e) => { setRemoveRole(e.target.value); setRemoveIds([]); }}>
                <option value="reasoner">Reasoning</option>
                <option value="coder">Coder</option>
                <option value="vlm">Image</option>
              </select>
            </div>
            <div className="field">
              <label>Models</label>
              <div className="checkbox-list">
                {(state.modelInfo?.[removeRole]?.options || []).filter((o: any) => o.id !== "best").map((opt: any) => (
                  <label key={opt.id} className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={removeIds.includes(opt.id)}
                      onChange={(e) => {
                        if (e.target.checked) setRemoveIds([...removeIds, opt.id]);
                        else setRemoveIds(removeIds.filter((id) => id !== opt.id));
                      }}
                    />
                    <span>{opt.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="row">
              <button onClick={() => setShowRemoveModel(false)}>Cancel</button>
              <button
                onClick={() => {
                  vscode?.postMessage({ type: "action", action: "removeModel", role: removeRole, model_ids: removeIds });
                  setShowRemoveModel(false);
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
