import * as vscode from "vscode";
import { ApiClient } from "./apiClient";
import { applyPatch } from "./patchApply";
import { gatherContext } from "./context";
import { ChatMessage, PendingPatch } from "./types";

class AgentViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "localCodeAgent.chatView";

  private view?: vscode.WebviewView;
  private output = vscode.window.createOutputChannel("Local Code Agent");
  private api = new ApiClient((msg) => this.output.appendLine(msg));
  private messages: ChatMessage[] = [];
  private progress: { text: string; status: "running" | "done" | "error" }[] = [];
  private pending: PendingPatch | null = null;
  private status: string = "Not connected";
  private serverUrl: string = "";
  private modelInfo: any = null;
  private modelStatusText: string = "";
  private mcpStatusText: string = "";
  private snapshotsText: string = "";
  private ingestStatusText: string = "";

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "dist-webview")],
    };
    view.webview.html = this.getHtml(view.webview);
    view.webview.onDidReceiveMessage(async (msg: any) => {
      switch (msg.type) {
        case "send":
          await this.handleUserMessage(msg.text);
          break;
        case "action":
          await this.runAction(msg.action, msg);
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
    await this.loadModels(false);
    await this.snapshotsRefresh();
    this.refresh();
  }

  private async handleUserMessage(text: string): Promise<void> {
    if (!text || !text.trim()) {
      return;
    }
    this.messages.push({ role: "user", text, timestamp: Date.now() });
    this.progress = [];
    this.pushProgress("Connecting to server…", "running");
    this.refresh();
    await this.ensureInit();
    this.markProgressDone("Connecting to server…");
    this.pushProgress("Planning request…", "running");
    const res = await this.api.post<{
      state: string;
      questions?: string[];
      plan?: string[];
      answer?: string | null;
      intent?: string;
      needs_confirm?: boolean;
      confirm_token?: string | null;
    }>("/query", { user_text: text });
    if (!res.ok) {
      this.markProgressError("Planning request…");
      this.messages.push({ role: "assistant", text: `Query failed: ${res.error}`, timestamp: Date.now() });
      this.refresh();
      return;
    }
    this.markProgressDone("Planning request…");
    if (res.data.answer) {
      this.pushProgress("Answer ready.", "done");
      this.messages.push({ role: "assistant", text: res.data.answer, timestamp: Date.now() });
      this.refresh();
      return;
    }
    if (res.data.questions && res.data.questions.length > 0) {
      this.pushProgress("Need clarification before proceeding.", "done");
      this.messages.push({ role: "assistant", text: res.data.questions.join("\n"), timestamp: Date.now() });
      this.refresh();
      return;
    }
    if (res.data.plan && res.data.plan.length > 0) {
      this.pushProgress("Plan ready.", "done");
      this.messages.push({ role: "assistant", text: res.data.plan.map((p) => `- ${p}`).join("\n"), timestamp: Date.now() });
      this.refresh();
      return;
    }
    this.pushProgress("Plan ready.", "done");
    this.messages.push({ role: "assistant", text: "Response received, but no answer or plan was provided.", timestamp: Date.now() });
    this.refresh();
  }

  public async runAction(action: string, payload: any = {}): Promise<void> {
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
        await this.approve(payload.diff);
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

  private async approve(diffOverride?: string): Promise<void> {
    if (!this.pending) {
      vscode.window.showWarningMessage("No pending patch");
      return;
    }
    const diffToApply = diffOverride || this.pending.diff;
    // Prefer server-side approve (works for VM + local)
    const serverRes = await this.api.post<any>("/approve", { unified_diff: diffToApply });
    if (serverRes.ok) {
      this.messages.push({ role: "assistant", text: "Approved on server", timestamp: Date.now() });
      this.pending = null;
      await this.api.post<any>("/reset_context", {});
      this.refresh();
      return;
    }
    // Fallback to local apply
    const res = await applyPatch(diffToApply);
    if (!res.ok) {
      vscode.window.showErrorMessage(res.message);
      await this.openDiffPreview(diffToApply);
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

  private async snapshotsRefresh(): Promise<void> {
    const res = await this.api.get<any>("/snapshots");
    if (!res.ok) {
      vscode.window.showErrorMessage(`Snapshots failed: ${res.error}`);
      return;
    }
    this.snapshotsText = JSON.stringify(res.data, null, 2);
    this.refresh();
  }

  private async snapshotCreate(): Promise<void> {
    const message = await vscode.window.showInputBox({ prompt: "Snapshot message (optional)" });
    const res = await this.api.post<any>("/snapshots/create", { message });
    if (!res.ok) {
      vscode.window.showErrorMessage(`Snapshot create failed: ${res.error}`);
      return;
    }
    this.snapshotsText = JSON.stringify(res.data, null, 2);
    this.refresh();
  }

  private async snapshotRestore(): Promise<void> {
    const id = await vscode.window.showInputBox({ prompt: "Snapshot ID to restore" });
    if (!id) return;
    const res = await this.api.post<any>("/snapshots/restore", { snapshot_id: id });
    if (!res.ok) {
      vscode.window.showErrorMessage(`Snapshot restore failed: ${res.error}`);
      return;
    }
    this.snapshotsText = JSON.stringify(res.data, null, 2);
    this.refresh();
  }

  private async loadModels(callInit: boolean = true): Promise<void> {
    if (callInit) {
      await this.ensureInit();
    }
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
        snapshotsText: this.snapshotsText,
        ingestStatusText: this.ingestStatusText,
        progress: this.progress,
      });
    }
  }

  private pushProgress(text: string, status: "running" | "done" | "error"): void {
    this.progress.push({ text, status });
  }

  private markProgressDone(text: string): void {
    const item = this.progress.find((p) => p.text === text);
    if (item) item.status = "done";
  }

  private markProgressError(text: string): void {
    const item = this.progress.find((p) => p.text === text);
    if (item) item.status = "error";
  }

  private getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "dist-webview", "index.js")
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "dist-webview", "index.css")
    );
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Local Code Agent</title>
  <link rel="stylesheet" href="${styleUri}">
</head>
<body>
  <div id="root"></div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
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

  // Best-effort: open the view container and ping server on activation
  setTimeout(() => {
    vscode.commands.executeCommand("workbench.view.extension.localCodeAgent");
    vscode.commands.executeCommand("localCodeAgent.ping");
  }, 500);
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
