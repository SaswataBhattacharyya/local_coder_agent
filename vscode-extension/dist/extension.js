"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const apiClient_1 = require("./apiClient");
const patchApply_1 = require("./patchApply");
const context_1 = require("./context");
class AgentViewProvider {
    constructor(context) {
        this.context = context;
        this.api = new apiClient_1.ApiClient();
        this.messages = [];
        this.pending = null;
        this.status = "Not connected";
        this.serverUrl = "";
        this.modelInfo = null;
        this.modelStatusText = "";
        this.mcpStatusText = "";
        this.snapshotsText = "";
        this.ingestStatusText = "";
    }
    resolveWebviewView(view) {
        this.view = view;
        view.webview.options = { enableScripts: true };
        view.webview.html = this.getHtml(view.webview);
        view.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.type) {
                case "send":
                    await this.handleUserMessage(msg.text);
                    break;
                case "action":
                    await this.runAction(msg.action);
                    break;
                case "selectModel":
                    await this.selectModel(msg.role, msg.modelId);
                    break;
            }
        });
        this.refresh();
    }
    async ensureInit() {
        const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!root) {
            this.status = "No workspace folder";
            this.refresh();
            return;
        }
        this.serverUrl = vscode.workspace.getConfiguration("localCodeAgent").get("serverUrl", "");
        const res = await this.api.post("/init", {
            repo_root: root,
            allow_missing_repo: true,
        });
        if (!res.ok) {
            this.status = `Init failed: ${res.error}`;
            this.refresh();
            return;
        }
        this.status = "Connected";
        await this.loadModels(false);
        await this.snapshotsRefresh();
        this.refresh();
    }
    async handleUserMessage(text) {
        if (!text || !text.trim()) {
            return;
        }
        this.messages.push({ role: "user", text, timestamp: Date.now() });
        this.refresh();
        await this.ensureInit();
        const res = await this.api.post("/query", { user_text: text });
        if (!res.ok) {
            this.messages.push({ role: "assistant", text: `Query failed: ${res.error}`, timestamp: Date.now() });
            this.refresh();
            return;
        }
        if (res.data.questions && res.data.questions.length > 0) {
            this.messages.push({ role: "assistant", text: res.data.questions.join("\n"), timestamp: Date.now() });
            this.refresh();
        }
    }
    async runAction(action) {
        switch (action) {
            case "ping":
                await this.ping();
                break;
            case "propose":
                await this.propose();
                break;
            case "revise":
                await this.revise();
                break;
            case "approve":
                await this.approve();
                break;
            case "reject":
                await this.reject();
                break;
            case "reset":
                await this.resetContext();
                break;
            case "mcpAllow":
                await this.mcpAllow();
                break;
            case "mcpRevoke":
                await this.mcpRevoke();
                break;
            case "mcpStatus":
                await this.mcpStatus();
                break;
            case "mcpReload":
                await this.mcpReload();
                break;
            case "modelsRefresh":
                await this.loadModels(true);
                break;
            case "snapshotsRefresh":
                await this.snapshotsRefresh();
                break;
            case "snapshotCreate":
                await this.snapshotCreate();
                break;
            case "snapshotRestore":
                await this.snapshotRestore();
                break;
        }
    }
    async ping() {
        this.serverUrl = vscode.workspace.getConfiguration("localCodeAgent").get("serverUrl", "");
        const res = await this.api.get("/mcp/status");
        if (res.ok) {
            this.status = "Connected";
            this.mcpStatusText = JSON.stringify(res.data, null, 2);
        }
        else {
            this.status = `Ping failed: ${res.error}`;
        }
        this.refresh();
    }
    async propose() {
        const instruction = await vscode.window.showInputBox({ prompt: "Describe the change" });
        if (!instruction) {
            return;
        }
        await this.ensureInit();
        const payload = { instruction };
        const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get("sendContextBundle", false);
        if (sendContext) {
            const bundle = await (0, context_1.gatherContext)();
            payload.context = bundle;
        }
        const res = await this.api.post("/propose", payload);
        if (!res.ok) {
            this.messages.push({ role: "assistant", text: `Propose failed: ${res.error}`, timestamp: Date.now() });
            this.refresh();
            return;
        }
        this.ingestStatusText = formatIngestStatus(res.data.ingest);
        this.pending = {
            diff: res.data.diff || "",
            summary: res.data.summary || "",
            riskNotes: res.data.risk_notes || "",
        };
        this.messages.push({ role: "assistant", text: res.data.summary || "Proposal ready", timestamp: Date.now() });
        this.refresh();
    }
    async revise() {
        if (!this.pending) {
            vscode.window.showWarningMessage("No pending patch to revise");
            return;
        }
        const instruction = await vscode.window.showInputBox({ prompt: "How should the pending patch change?" });
        if (!instruction) {
            return;
        }
        const payload = { instruction };
        const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get("sendContextBundle", false);
        if (sendContext) {
            const bundle = await (0, context_1.gatherContext)();
            payload.context = bundle;
        }
        const res = await this.api.post("/revise_pending", payload);
        if (!res.ok) {
            this.messages.push({ role: "assistant", text: `Revise failed: ${res.error}`, timestamp: Date.now() });
            this.refresh();
            return;
        }
        this.ingestStatusText = formatIngestStatus(res.data.ingest);
        this.pending = {
            diff: res.data.diff || "",
            summary: res.data.summary || "",
            riskNotes: res.data.risk_notes || "",
        };
        this.messages.push({ role: "assistant", text: res.data.summary || "Revised pending patch", timestamp: Date.now() });
        this.refresh();
    }
    async approve() {
        if (!this.pending) {
            vscode.window.showWarningMessage("No pending patch");
            return;
        }
        const res = await (0, patchApply_1.applyPatch)(this.pending.diff);
        if (!res.ok) {
            vscode.window.showErrorMessage(res.message);
            await this.openDiffPreview(this.pending.diff);
            return;
        }
        this.messages.push({ role: "assistant", text: res.message, timestamp: Date.now() });
        this.pending = null;
        await this.api.post("/reset_context", {});
        this.refresh();
    }
    async reject() {
        this.pending = null;
        await this.api.post("/reject", {});
        this.refresh();
    }
    async resetContext() {
        const res = await this.api.post("/reset_context", {});
        if (!res.ok) {
            vscode.window.showErrorMessage(`Reset failed: ${res.error}`);
            return;
        }
        this.messages.push({ role: "assistant", text: "Context reset", timestamp: Date.now() });
        this.pending = null;
        this.refresh();
    }
    async mcpAllow() {
        const res = await this.api.post("/mcp/allow", { confirm: "YES" });
        if (!res.ok) {
            vscode.window.showErrorMessage(`MCP allow failed: ${res.error}`);
            return;
        }
        this.messages.push({ role: "assistant", text: "MCP allowed", timestamp: Date.now() });
        this.refresh();
    }
    async mcpRevoke() {
        const res = await this.api.post("/mcp/revoke", { confirm: "YES" });
        if (!res.ok) {
            vscode.window.showErrorMessage(`MCP revoke failed: ${res.error}`);
            return;
        }
        this.messages.push({ role: "assistant", text: "MCP revoked", timestamp: Date.now() });
        this.refresh();
    }
    async mcpStatus() {
        const res = await this.api.get("/mcp/status");
        if (!res.ok) {
            vscode.window.showErrorMessage(`MCP status failed: ${res.error}`);
            return;
        }
        this.mcpStatusText = JSON.stringify(res.data, null, 2);
        this.messages.push({ role: "assistant", text: "MCP status updated", timestamp: Date.now() });
        this.refresh();
    }
    async mcpReload() {
        const res = await this.api.post("/mcp/reload", {});
        if (!res.ok) {
            vscode.window.showErrorMessage(`MCP reload failed: ${res.error}`);
            return;
        }
        this.messages.push({ role: "assistant", text: "MCP config reloaded", timestamp: Date.now() });
        await this.mcpStatus();
    }
    async snapshotsRefresh() {
        const res = await this.api.get("/snapshots");
        if (!res.ok) {
            vscode.window.showErrorMessage(`Snapshots failed: ${res.error}`);
            return;
        }
        this.snapshotsText = JSON.stringify(res.data, null, 2);
        this.refresh();
    }
    async snapshotCreate() {
        const message = await vscode.window.showInputBox({ prompt: "Snapshot message (optional)" });
        const res = await this.api.post("/snapshots/create", { message });
        if (!res.ok) {
            vscode.window.showErrorMessage(`Snapshot create failed: ${res.error}`);
            return;
        }
        this.snapshotsText = JSON.stringify(res.data, null, 2);
        this.refresh();
    }
    async snapshotRestore() {
        const id = await vscode.window.showInputBox({ prompt: "Snapshot ID to restore" });
        if (!id)
            return;
        const res = await this.api.post("/snapshots/restore", { snapshot_id: id });
        if (!res.ok) {
            vscode.window.showErrorMessage(`Snapshot restore failed: ${res.error}`);
            return;
        }
        this.snapshotsText = JSON.stringify(res.data, null, 2);
        this.refresh();
    }
    async loadModels(callInit = true) {
        if (callInit) {
            await this.ensureInit();
        }
        const res = await this.api.get("/models");
        if (!res.ok) {
            this.modelInfo = null;
            return;
        }
        this.modelInfo = res.data;
        this.modelStatusText = this.buildModelStatus(res.data);
        this.refresh();
    }
    async selectModel(role, modelId) {
        const res = await this.api.post("/models/select", { role, model_id: modelId });
        if (!res.ok) {
            vscode.window.showErrorMessage(`Model select failed: ${res.error}`);
            return;
        }
        await this.loadModels();
    }
    buildModelStatus(data) {
        if (!data)
            return "";
        const rInfo = data.reasoner || {};
        const cInfo = data.coder || {};
        const r = rInfo.selected || "best";
        const c = cInfo.selected || "best";
        const rDefault = rInfo.default || "";
        const cDefault = cInfo.default || "";
        const rLabel = this.formatModelLabel(r, rInfo);
        const cLabel = this.formatModelLabel(c, cInfo);
        const rOut = r === "best" ? `best (${rDefault}) => ${rLabel}` : rLabel;
        const cOut = c === "best" ? `best (${cDefault}) => ${cLabel}` : cLabel;
        return `Reasoner: ${rOut}\nCoder: ${cOut}`;
    }
    formatModelLabel(id, info) {
        const opts = info?.options || [];
        const found = opts.find((o) => o.id === id) || {};
        const provider = found.provider ? ` (${found.provider})` : "";
        return `${id}${provider}`;
    }
    async openDiffPreview(diffText) {
        const doc = await vscode.workspace.openTextDocument({ content: diffText, language: "diff" });
        await vscode.window.showTextDocument(doc, { preview: true });
    }
    refresh() {
        if (this.view) {
            this.view.webview.postMessage({
                type: "state",
                messages: this.messages,
                pending: this.pending,
                status: this.status,
                serverUrl: this.serverUrl,
                modelInfo: this.modelInfo,
                modelStatusText: this.modelStatusText,
                mcpStatusText: this.mcpStatusText,
                snapshotsText: this.snapshotsText,
                ingestStatusText: this.ingestStatusText,
            });
        }
    }
    getHtml(webview) {
        const nonce = getNonce();
        return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Local Code Agent</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #4c8df6;
      --ok: #2ea043;
      --warn: #d29922;
      --border: #30363d;
    }
    body { font-family: Segoe UI, sans-serif; padding: 10px; color: var(--text); background: var(--bg); }
    h3 { margin: 10px 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
    .tabs { display:flex; gap:6px; margin: 6px 0 10px; }
    .tab { padding:6px 10px; border-radius: 8px; background: var(--panel); border:1px solid var(--border); cursor:pointer; font-size:12px; }
    .tab.active { background: var(--accent); border-color: var(--accent); color:#fff; }
    .section { display:none; }
    .section.active { display:block; }
    .chat { border: 1px solid var(--border); padding: 8px; border-radius: 8px; min-height: 120px; background: var(--panel); }
    .msg { margin: 6px 0; padding: 6px 8px; border-radius: 6px; white-space: pre-wrap; }
    .user { background: var(--ok); color: #fff; }
    .assistant { background: #1f6feb; color: #fff; }
    .system { background: #6e7681; color: #fff; }
    .pending { border: 1px dashed #58a6ff; padding: 8px; border-radius: 8px; background: var(--bg); }
    textarea { width: 100%; height: 64px; resize: vertical; }
    .row { display: flex; gap: 6px; flex-wrap: wrap; }
    button { background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; cursor: pointer; }
    button:hover { background: #3b4252; }
    .status { font-size: 12px; color: var(--muted); }
    .diff { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; white-space: pre; overflow-x: auto; background: #0b0f14; padding: 6px; border-radius: 6px; }
  </style>
</head>
<body>
  <div style="font-size:12px;color:#8b949e;margin-bottom:6px;">Local Code Agent</div>
  <div class="status">Status: <span id="status">Not connected</span></div>
  <div class="status">Server: <span id="serverUrl"></span></div>

  <div class="tabs">
    <div class="tab active" data-tab="chat">Chat</div>
    <div class="tab" data-tab="settings">Settings</div>
    <div class="tab" data-tab="history">History</div>
    <div class="tab" data-tab="mcp">MCP</div>
  </div>

  <div class="section active" id="tab-chat">
    <h3>Conversation</h3>
    <div class="chat" id="chat"></div>

    <h3>Input</h3>
    <textarea id="input" placeholder="Describe your request..."></textarea>
    <div class="row">
      <button id="send">Send</button>
      <button data-action="ping">Ping Server</button>
    </div>

    <h3>Pending Patch</h3>
    <div class="pending">
      <div><strong>Summary:</strong> <span id="summary"></span></div>
      <div><strong>Risk:</strong> <span id="risk"></span></div>
      <div><strong>Ingest:</strong> <span id="ingest"></span></div>
      <div class="diff" id="diff"></div>
    </div>

    <h3>Actions</h3>
    <div class="row">
      <button data-action="propose">Propose</button>
      <button data-action="revise">Revise Pending</button>
      <button data-action="approve">Approve Pending</button>
      <button data-action="reject">Reject Pending</button>
      <button data-action="reset">Reset Context</button>
    </div>
  </div>

  <div class="section" id="tab-settings">
    <h3>Models</h3>
    <div class="diff" id="modelStatus"></div>
    <div class="row">
      <label>Reasoner:</label>
      <select id="reasonerSelect"></select>
    </div>
    <div class="row">
      <label>Coder:</label>
      <select id="coderSelect"></select>
    </div>
    <div class="row">
      <button data-action="modelsRefresh">Refresh Models</button>
    </div>
  </div>

  <div class="section" id="tab-history">
    <h3>Snapshots</h3>
    <div class="row">
      <button data-action="snapshotsRefresh">Refresh</button>
      <button data-action="snapshotCreate">Create Snapshot</button>
      <button data-action="snapshotRestore">Restore Snapshot</button>
    </div>
    <div class="diff" id="snapshotsStatus"></div>
  </div>

  <div class="section" id="tab-mcp">
    <h3>MCP</h3>
    <div class="row">
      <button data-action="mcpAllow">MCP Allow</button>
      <button data-action="mcpRevoke">MCP Revoke</button>
      <button data-action="mcpStatus">MCP Status</button>
      <button data-action="mcpReload">MCP Reload</button>
    </div>
    <div class="diff" id="mcpStatus"></div>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const chatEl = document.getElementById("chat");
    const inputEl = document.getElementById("input");
    const statusEl = document.getElementById("status");
    const urlEl = document.getElementById("serverUrl");
    const reasonerSelect = document.getElementById("reasonerSelect");
    const coderSelect = document.getElementById("coderSelect");
    const modelStatusEl = document.getElementById("modelStatus");
    const mcpStatusEl = document.getElementById("mcpStatus");
    const snapshotsEl = document.getElementById("snapshotsStatus");
    const ingestEl = document.getElementById("ingest");
    const summaryEl = document.getElementById("summary");
    const riskEl = document.getElementById("risk");
    const diffEl = document.getElementById("diff");

    document.getElementById("send").addEventListener("click", () => {
      vscode.postMessage({ type: "send", text: inputEl.value });
      inputEl.value = "";
    });

    document.querySelectorAll("button[data-action]").forEach(btn => {
      btn.addEventListener("click", () => {
        vscode.postMessage({ type: "action", action: btn.dataset.action });
      });
    });

    document.querySelectorAll(".tab").forEach(tab => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".section").forEach(s => s.classList.remove("active"));
        tab.classList.add("active");
        const target = document.getElementById("tab-" + tab.dataset.tab);
        if (target) target.classList.add("active");
      });
    });

    function fillSelect(selectEl, modelInfo, role) {
      if (!selectEl || !modelInfo || !modelInfo[role]) return;
      const info = modelInfo[role];
      selectEl.innerHTML = "";
      (info.options || []).forEach(opt => {
        const o = document.createElement("option");
        o.value = opt.id;
        o.textContent = opt.label || opt.id;
        selectEl.appendChild(o);
      });
      selectEl.value = info.selected || "best";
    }

    if (reasonerSelect) {
      reasonerSelect.addEventListener("change", () => {
        vscode.postMessage({ type: "selectModel", role: "reasoner", modelId: reasonerSelect.value });
      });
    }
    if (coderSelect) {
      coderSelect.addEventListener("change", () => {
        vscode.postMessage({ type: "selectModel", role: "coder", modelId: coderSelect.value });
      });
    }

    window.addEventListener("message", (event) => {
      const msg = event.data;
      if (msg.type === "state") {
        statusEl.textContent = msg.status || "";
        urlEl.textContent = msg.serverUrl || "";
        fillSelect(reasonerSelect, msg.modelInfo, "reasoner");
        fillSelect(coderSelect, msg.modelInfo, "coder");
        modelStatusEl.textContent = msg.modelStatusText || "";
        mcpStatusEl.textContent = msg.mcpStatusText || "";
        snapshotsEl.textContent = msg.snapshotsText || "";
        ingestEl.textContent = msg.ingestStatusText || "";
        chatEl.innerHTML = "";
        (msg.messages || []).forEach(m => {
          const div = document.createElement("div");
          div.className = "msg " + m.role;
          div.textContent = m.text;
          chatEl.appendChild(div);
        });
        if (msg.pending) {
          summaryEl.textContent = msg.pending.summary || "";
          riskEl.textContent = msg.pending.riskNotes || "";
          diffEl.textContent = msg.pending.diff || "";
        } else {
          summaryEl.textContent = "";
          riskEl.textContent = "";
          diffEl.textContent = "";
        }
      }
    });
  </script>
</body>
</html>`;
    }
}
AgentViewProvider.viewType = "localCodeAgent.chatView";
function activate(context) {
    const provider = new AgentViewProvider(context);
    context.subscriptions.push(vscode.window.registerWebviewViewProvider(AgentViewProvider.viewType, provider));
    provider.runAction("modelsRefresh");
    const commands = [
        ["localCodeAgent.ping", () => provider.runAction("ping")],
        ["localCodeAgent.propose", () => provider.runAction("propose")],
        ["localCodeAgent.revise", () => provider.runAction("revise")],
        ["localCodeAgent.approve", () => provider.runAction("approve")],
        ["localCodeAgent.reject", () => provider.runAction("reject")],
        ["localCodeAgent.resetContext", () => provider.runAction("reset")],
        ["localCodeAgent.mcpAllow", () => provider.runAction("mcpAllow")],
        ["localCodeAgent.mcpRevoke", () => provider.runAction("mcpRevoke")],
        ["localCodeAgent.mcpStatus", () => provider.runAction("mcpStatus")],
    ];
    for (const [cmd, fn] of commands) {
        context.subscriptions.push(vscode.commands.registerCommand(cmd, fn));
    }
    // Best-effort: open the view container and ping server on activation
    setTimeout(() => {
        vscode.commands.executeCommand("workbench.view.extension.localCodeAgent");
        vscode.commands.executeCommand("localCodeAgent.ping");
    }, 500);
}
function deactivate() { }
function getNonce() {
    let text = "";
    const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    for (let i = 0; i < 32; i++) {
        text += possible.charAt(Math.floor(Math.random() * possible.length));
    }
    return text;
}
function formatIngestStatus(ingest) {
    if (!ingest)
        return "";
    if (!ingest.used)
        return "not used";
    const chunks = ingest.chunks ?? "?";
    const top = ingest.top_k ?? "?";
    return `used (${top} of ${chunks} chunks)`;
}
