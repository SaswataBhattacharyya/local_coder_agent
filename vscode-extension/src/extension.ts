import * as vscode from "vscode";
import { ApiClient } from "./apiClient";
import { applyPatch } from "./patchApply";
import { gatherContext } from "./context";
import { ChatMessage, PendingPatch } from "./types";

class AgentViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "localCodeAgent.chatView";

  private view?: vscode.WebviewView;
  private api = new ApiClient();
  private messages: ChatMessage[] = [];
  private pending: PendingPatch | null = null;
  private status: string = "Not connected";
  private serverUrl: string = "";
  private modelInfo: any = null;
  private modelStatusText: string = "";
  private mcpStatusText: string = "";
  private restoreStatusText: string = "";
  private ingestStatusText: string = "";

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView): void {
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

  private async ensureInit(): Promise<void> {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) {
      this.status = "No workspace folder";
      this.refresh();
      return;
    }
    this.serverUrl = vscode.workspace.getConfiguration("localCodeAgent").get<string>("serverUrl", "");
    const res = await this.api.post<{ status: string }>("/init", {
      repo_root: root,
      allow_missing_repo: true,
    });
    if (!res.ok) {
      this.status = `Init failed: ${res.error}`;
      this.refresh();
      return;
    }
    this.status = "Connected";
    await this.loadModels();
    this.refresh();
  }

  private async handleUserMessage(text: string): Promise<void> {
    if (!text || !text.trim()) {
      return;
    }
    this.messages.push({ role: "user", text, timestamp: Date.now() });
    this.refresh();
    await this.ensureInit();
    const res = await this.api.post<{ state: string; questions: string[] }>("/query", { user_text: text });
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

  public async runAction(action: string): Promise<void> {
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
        await this.loadModels();
        break;
      case "restoreSet":
        await this.setRestoreRemote();
        break;
      case "restoreDisable":
        await this.disableRestoreRemote();
        break;
    }
  }

  private async ping(): Promise<void> {
    this.serverUrl = vscode.workspace.getConfiguration("localCodeAgent").get<string>("serverUrl", "");
    const res = await this.api.get<{ mcp_allowed: boolean }>("/mcp/status");
    if (res.ok) {
      this.status = "Connected";
      this.mcpStatusText = JSON.stringify(res.data, null, 2);
    } else {
      this.status = `Ping failed: ${res.error}`;
    }
    this.refresh();
  }

  private async propose(): Promise<void> {
    const instruction = await vscode.window.showInputBox({ prompt: "Describe the change" });
    if (!instruction) {
      return;
    }
    await this.ensureInit();
    const payload: any = { instruction };
    const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("sendContextBundle", false);
    if (sendContext) {
      const bundle = await gatherContext();
      payload.context = bundle;
    }
    const res = await this.api.post<any>("/propose", payload);
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

  private async revise(): Promise<void> {
    if (!this.pending) {
      vscode.window.showWarningMessage("No pending patch to revise");
      return;
    }
    const instruction = await vscode.window.showInputBox({ prompt: "How should the pending patch change?" });
    if (!instruction) {
      return;
    }
    const payload: any = { instruction };
    const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("sendContextBundle", false);
    if (sendContext) {
      const bundle = await gatherContext();
      payload.context = bundle;
    }
    const res = await this.api.post<any>("/revise_pending", payload);
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

  private async approve(): Promise<void> {
    if (!this.pending) {
      vscode.window.showWarningMessage("No pending patch");
      return;
    }
    const res = await applyPatch(this.pending.diff);
    if (!res.ok) {
      vscode.window.showErrorMessage(res.message);
      await this.openDiffPreview(this.pending.diff);
      return;
    }
    this.messages.push({ role: "assistant", text: res.message, timestamp: Date.now() });
    this.pending = null;
    await this.api.post<any>("/reset_context", {});
    this.refresh();
  }

  private async reject(): Promise<void> {
    this.pending = null;
    await this.api.post<any>("/reject", {});
    this.refresh();
  }

  private async resetContext(): Promise<void> {
    const res = await this.api.post<any>("/reset_context", {});
    if (!res.ok) {
      vscode.window.showErrorMessage(`Reset failed: ${res.error}`);
      return;
    }
    this.messages.push({ role: "assistant", text: "Context reset", timestamp: Date.now() });
    this.pending = null;
    this.refresh();
  }

  private async mcpAllow(): Promise<void> {
    const res = await this.api.post<any>("/mcp/allow", { confirm: "YES" });
    if (!res.ok) {
      vscode.window.showErrorMessage(`MCP allow failed: ${res.error}`);
      return;
    }
    this.messages.push({ role: "assistant", text: "MCP allowed", timestamp: Date.now() });
    this.refresh();
  }

  private async mcpRevoke(): Promise<void> {
    const res = await this.api.post<any>("/mcp/revoke", { confirm: "YES" });
    if (!res.ok) {
      vscode.window.showErrorMessage(`MCP revoke failed: ${res.error}`);
      return;
    }
    this.messages.push({ role: "assistant", text: "MCP revoked", timestamp: Date.now() });
    this.refresh();
  }

  private async mcpStatus(): Promise<void> {
    const res = await this.api.get<any>("/mcp/status");
    if (!res.ok) {
      vscode.window.showErrorMessage(`MCP status failed: ${res.error}`);
      return;
    }
    this.mcpStatusText = JSON.stringify(res.data, null, 2);
    this.messages.push({ role: "assistant", text: "MCP status updated", timestamp: Date.now() });
    this.refresh();
  }

  private async mcpReload(): Promise<void> {
    const res = await this.api.post<any>("/mcp/reload", {});
    if (!res.ok) {
      vscode.window.showErrorMessage(`MCP reload failed: ${res.error}`);
      return;
    }
    this.messages.push({ role: "assistant", text: "MCP config reloaded", timestamp: Date.now() });
    await this.mcpStatus();
  }

  private async loadModels(): Promise<void> {
    const res = await this.api.get<any>("/models");
    if (!res.ok) {
      this.modelInfo = null;
      return;
    }
    this.modelInfo = res.data;
    this.modelStatusText = this.buildModelStatus(res.data);
    this.refresh();
  }

  private async selectModel(role: "reasoner" | "coder", modelId: string): Promise<void> {
    const res = await this.api.post<any>("/models/select", { role, model_id: modelId });
    if (!res.ok) {
      vscode.window.showErrorMessage(`Model select failed: ${res.error}`);
      return;
    }
    await this.loadModels();
  }

  private buildModelStatus(data: any): string {
    if (!data) return "";
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

  private formatModelLabel(id: string, info: any): string {
    const opts = info?.options || [];
    const found = opts.find((o: any) => o.id === id) || {};
    const provider = found.provider ? ` (${found.provider})` : "";
    return `${id}${provider}`;
  }

  private async setRestoreRemote(): Promise<void> {
    const url = await vscode.window.showInputBox({ prompt: "Restore remote URL (leave blank to cancel)" });
    if (!url) {
      return;
    }
    const pushChoice = await vscode.window.showQuickPick(["push on approve", "do not push"], { placeHolder: "Push commits to remote on approval?" });
    const pushOnApprove = pushChoice === "push on approve";
    const res = await this.api.post<any>("/restore_remote", { restore_remote_url: url, push_on_approve: pushOnApprove });
    if (!res.ok) {
      vscode.window.showErrorMessage(`Restore remote failed: ${res.error}`);
      return;
    }
    this.restoreStatusText = JSON.stringify(res.data, null, 2);
    this.refresh();
  }

  private async disableRestoreRemote(): Promise<void> {
    const res = await this.api.post<any>("/restore_remote", { restore_remote_url: "" });
    if (!res.ok) {
      vscode.window.showErrorMessage(`Disable restore remote failed: ${res.error}`);
      return;
    }
    this.restoreStatusText = JSON.stringify(res.data, null, 2);
    this.refresh();
  }

  private async openDiffPreview(diffText: string): Promise<void> {
    const doc = await vscode.workspace.openTextDocument({ content: diffText, language: "diff" });
    await vscode.window.showTextDocument(doc, { preview: true });
  }

  private refresh(): void {
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
        restoreStatusText: this.restoreStatusText,
        ingestStatusText: this.ingestStatusText,
      });
    }
  }

  private getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Local Code Agent</title>
  <style>
    body { font-family: Segoe UI, sans-serif; padding: 10px; color: #e6edf3; background: #0d1117; }
    h3 { margin: 10px 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: #8b949e; }
    .chat { border: 1px solid #30363d; padding: 8px; border-radius: 8px; min-height: 120px; background: #161b22; }
    .msg { margin: 6px 0; padding: 6px 8px; border-radius: 6px; white-space: pre-wrap; }
    .user { background: #238636; color: #fff; }
    .assistant { background: #1f6feb; color: #fff; }
    .system { background: #6e7681; color: #fff; }
    .pending { border: 1px dashed #58a6ff; padding: 8px; border-radius: 8px; background: #0d1117; }
    textarea { width: 100%; height: 64px; resize: vertical; }
    .row { display: flex; gap: 6px; flex-wrap: wrap; }
    button { background: #30363d; color: #e6edf3; border: 1px solid #30363d; border-radius: 6px; padding: 6px 8px; cursor: pointer; }
    button:hover { background: #3b4252; }
    .status { font-size: 12px; color: #8b949e; }
    .diff { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; white-space: pre; overflow-x: auto; background: #0b0f14; padding: 6px; border-radius: 6px; }
  </style>
</head>
<body>
  <div class="status">Status: <span id="status">Not connected</span></div>
  <div class="status">Server: <span id="serverUrl"></span></div>

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

  <h3>MCP</h3>
  <div class="row">
    <button data-action="mcpAllow">MCP Allow</button>
    <button data-action="mcpRevoke">MCP Revoke</button>
    <button data-action="mcpStatus">MCP Status</button>
    <button data-action="mcpReload">MCP Reload</button>
  </div>
  <div class="diff" id="mcpStatus"></div>

  <h3>Restore Remote</h3>
  <div class="row">
    <button data-action="restoreSet">Set Restore Remote</button>
    <button data-action="restoreDisable">Disable Restore Remote</button>
  </div>
  <div class="diff" id="restoreStatus"></div>

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
    const restoreStatusEl = document.getElementById("restoreStatus");
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
        restoreStatusEl.textContent = msg.restoreStatusText || "";
        ingestEl.textContent = msg.ingestStatusText || "";
        chatEl.innerHTML = "";
        (msg.messages || []).forEach(m => {
          const div = document.createElement("div");
          div.className = `msg ${m.role}`;
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

export function activate(context: vscode.ExtensionContext) {
  const provider = new AgentViewProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(AgentViewProvider.viewType, provider)
  );

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
  ] as const;

  for (const [cmd, fn] of commands) {
    context.subscriptions.push(vscode.commands.registerCommand(cmd, fn));
  }
}

export function deactivate() {}

function getNonce() {
  let text = "";
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}

function formatIngestStatus(ingest: any): string {
  if (!ingest) return "";
  if (!ingest.used) return "not used";
  const chunks = ingest.chunks ?? "?";
  const top = ingest.top_k ?? "?";
  return `used (${top} of ${chunks} chunks)`;
}
