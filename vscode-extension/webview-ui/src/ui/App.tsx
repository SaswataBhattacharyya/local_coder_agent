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
  mcpStatusText: string;
  snapshotsText: string;
  ingestStatusText: string;
  progress?: { text: string; status: "running" | "done" | "error" }[];
};

const vscode = (window as any).acquireVsCodeApi?.();

export const App: React.FC = () => {
  const [tab, setTab] = useState<"chat" | "settings" | "history" | "mcp">("chat");
  const [state, setState] = useState<AgentState>({
    status: "Not connected",
    serverUrl: "",
    messages: [],
    pending: null,
    modelStatusText: "",
    mcpStatusText: "",
    snapshotsText: "",
    ingestStatusText: "",
    progress: [],
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

  const send = () => {
    const input = (document.getElementById("input") as HTMLTextAreaElement | null);
    const text = input?.value?.trim() || "";
    if (!text) return;
    vscode?.postMessage({ type: "send", text });
    if (input) input.value = "";
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

  return (
    <div className="app">
      <div className="header">
        <div className="title">Local Code Agent</div>
        <div className="status">Status: {state.status}</div>
        <div className="status">Server: {state.serverUrl}</div>
      </div>

      <div className="tabs">
        {["chat", "settings", "history", "mcp"].map((t) => (
          <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t as any)}>
            {t.toUpperCase()}
          </button>
        ))}
      </div>

      {tab === "chat" && (
        <div className="section chat-section">
          {state.progress && state.progress.length > 0 && (
            <div className="progress">
              {state.progress.map((p, i) => (
                <div key={i} className={`progress-item ${p.status}`}>
                  <span className={p.status === "running" ? "dotdot" : ""}>{p.text}</span>
                </div>
              ))}
            </div>
          )}
          <div className="chat">
            {state.messages.map((m, idx) => (
              <div key={idx} className={`msg ${m.role}`}>
                <span>{m.text}</span>
                {m.streaming && <span className="streaming-dots" />}
              </div>
            ))}
          </div>
          <textarea id="input" placeholder="Describe your request..." />
          <div className="row">
            <button onClick={send}>Send</button>
            <button onClick={() => runAction("ping")}>Ping Server</button>
          </div>

          <div className="pending">
            <div><strong>Summary:</strong> {state.pending?.summary || ""}</div>
            <div><strong>Risk:</strong> {state.pending?.riskNotes || ""}</div>
            <div><strong>Ingest:</strong> {state.ingestStatusText || ""}</div>
          </div>

          <div className="actions">
            <button onClick={() => runAction("propose")}>Propose</button>
            <button onClick={() => runAction("revise")}>Revise Pending</button>
            <button onClick={approveSelected}>Approve Selected</button>
            <button onClick={() => runAction("reject")}>Reject Pending</button>
            <button onClick={() => runAction("reset")}>Reset Context</button>
          </div>
        </div>
      )}

      {tab === "settings" && (
        <div className="section">
          <div className="panel">
            <h3>Models</h3>
            <pre className="diff">{state.modelStatusText}</pre>
            <div className="row">
              <button onClick={() => runAction("modelsRefresh")}>Refresh Models</button>
            </div>
          </div>
        </div>
      )}

      {tab === "history" && (
        <div className="section">
          <div className="row">
            <button onClick={() => runAction("snapshotsRefresh")}>Refresh</button>
            <button onClick={() => runAction("snapshotCreate")}>Create Snapshot</button>
            <button onClick={() => runAction("snapshotRestore")}>Restore Snapshot</button>
          </div>
          <pre className="diff">{state.snapshotsText}</pre>
        </div>
      )}

      {tab === "mcp" && (
        <div className="section">
          <div className="row">
            <button onClick={() => runAction("mcpAllow")}>MCP Allow</button>
            <button onClick={() => runAction("mcpRevoke")}>MCP Revoke</button>
            <button onClick={() => runAction("mcpStatus")}>MCP Status</button>
            <button onClick={() => runAction("mcpReload")}>MCP Reload</button>
          </div>
          <pre className="diff">{state.mcpStatusText}</pre>
        </div>
      )}

      {tab === "chat" && state.pending?.diff && (
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
    </div>
  );
};
