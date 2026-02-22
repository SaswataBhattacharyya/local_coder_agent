import * as vscode from "vscode";
import { ApiClient } from "./apiClient";
import { applyPatch } from "./patchApply";
import { gatherContext, gatherWorkspaceContext } from "./context";
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
  private warnedMissingContext: boolean = false;
  private currentStreamReader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private indexStatus: any = null;
  private indexPoller: NodeJS.Timeout | null = null;
  private lastIndexEventId: number = 0;
  private repoRoot: string = "";
  private repoRootStateless: boolean = false;
  private repoRootRequested: string = "";
  private inferenceConfig: any = null;

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
          await this.handleUserMessage(msg.text, msg.images || []);
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
    this.startIndexPolling();
  }

  private async ensureInit(): Promise<void> {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) {
      this.status = "No workspace folder";
      this.refresh();
      return;
    }
    this.serverUrl = vscode.workspace.getConfiguration("localCodeAgent").get<string>("serverUrl", "");
    const res = await this.api.post<{ status: string; repo_root?: string; repo_root_stateless?: boolean; requested_repo_root?: string }>("/init", {
      repo_root: root,
      allow_missing_repo: true,
    });
    if (!res.ok) {
      this.status = `Init failed: ${res.error}`;
      this.refresh();
      return;
    }
    this.status = "Connected";
    this.repoRoot = res.data.repo_root || "";
    this.repoRootStateless = Boolean(res.data.repo_root_stateless);
    this.repoRootRequested = res.data.requested_repo_root || root;
    await this.loadModels(false);
    await this.loadInferenceConfig();
    await this.snapshotsRefresh();
    await this.refreshIndexStatus();
    this.refresh();
  }

  private async handleUserMessage(text: string, images: any[] = []): Promise<void> {
    if (!text || !text.trim()) {
      return;
    }
    const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("sendContextBundle", false);
    if (!sendContext && !this.warnedMissingContext) {
      this.warnedMissingContext = true;
      this.messages.push({
        role: "assistant",
        text: "Heads up: sendContextBundle is disabled. In VM mode the server cannot see your local files, so summaries may be empty. Enable localCodeAgent.sendContextBundle.",
        timestamp: Date.now(),
      });
    }
    const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("useStreaming", false);
    if (useStreaming) {
      await this.handleUserMessageStream(text, images);
      return;
    }
    this.messages.push({ role: "user", text, timestamp: Date.now() });
    this.progress = [];
    this.pushProgress("Connecting to server…", "running");
    this.refresh();
    await this.ensureInit();
    this.markProgressDone("Connecting to server…");
    this.pushProgress("Reading workspace files…", "running");
    const depth = vscode.workspace.getConfiguration("localCodeAgent").get<"shallow" | "standard" | "deep">("infoSummaryDepth", "standard");
    const workspaceContext = sendContext ? await gatherWorkspaceContext(depth) : null;
    this.markProgressDone("Reading workspace files…");
    this.pushProgress("Planning request…", "running");
    const res = await this.api.post<{
      state: string;
      questions?: string[];
      plan?: string[];
      answer?: string | null;
      intent?: string;
      needs_confirm?: boolean;
      confirm_token?: string | null;
    }>("/query", { user_text: text, workspace_context: workspaceContext, images });
    if (!res.ok) {
      this.markProgressError("Planning request…");
      this.messages.push({ role: "assistant", text: `Query failed: ${res.error}`, timestamp: Date.now() });
      this.refresh();
      return;
    }
    this.markProgressDone("Planning request…");
    if (res.data.answer) {
      this.pushProgress("Answer ready.", "done");
      if ((res.data as any).metrics) {
        const m = (res.data as any).metrics;
        this.pushProgress(`Metrics: in=${m.input_tokens} tok, out=${m.output_tokens} tok, chunks=${m.chunks_retrieved}`, "done");
      }
      if ((res.data as any).facts) {
        const f = (res.data as any).facts;
        this.pushProgress(`Facts: read=${f.files_read} files, bytes=${f.context_bytes}, backend=${f.backend}`, "done");
      }
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

  private async handleUserMessageStream(text: string, images: any[] = []): Promise<void> {
    this.messages.push({ role: "user", text, timestamp: Date.now() });
    this.progress = [];
    this.pushProgress("Connecting to server…", "running");
    this.refresh();
    await this.ensureInit();
    this.markProgressDone("Connecting to server…");
    this.pushProgress("Streaming response…", "running");

    const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("sendContextBundle", false);
    const depth = vscode.workspace.getConfiguration("localCodeAgent").get<"shallow" | "standard" | "deep">("infoSummaryDepth", "standard");
    this.pushProgress("Reading workspace files…", "running");
    const workspaceContext = sendContext ? await gatherWorkspaceContext(depth) : null;
    this.markProgressDone("Reading workspace files…");
    const res = await this.api.postStream("/query_stream", { user_text: text, workspace_context: workspaceContext, images });
    if (!res.ok) {
      this.markProgressError("Streaming response…");
      this.messages.push({ role: "assistant", text: `Query failed: ${res.error}`, timestamp: Date.now() });
      this.refresh();
      return;
    }

    const msgIndex = this.messages.length;
    this.currentStreamReader = res.reader;
    this.messages.push({ role: "assistant", text: "", timestamp: Date.now(), streaming: true });
    this.refresh();

    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { value, done } = await res.reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        this.handleSseEvent(raw, msgIndex);
      }
    }
      const current = this.messages[msgIndex];
      if (current && current.role === "assistant") {
        current.streaming = false;
      }
      this.currentStreamReader = null;
      this.markProgressDone("Streaming response…");
      this.refresh();
  }

  private handleSseEvent(raw: string, msgIndex: number): void {
    let eventName = "message";
    let data = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data += line.slice(5).trimStart() + "\n";
      }
    }
    data = data.replace(/\n$/, "");
    if (!data) {
      return;
    }
    if (eventName === "answer" || eventName === "plan" || eventName === "questions") {
      const current = this.messages[msgIndex];
      if (current && current.role === "assistant") {
        current.text += data;
      }
      this.refresh();
      return;
    }
    if (eventName === "status") {
      this.pushProgress(data, "running");
      this.refresh();
      return;
    }
    if (eventName === "metrics") {
      try {
        const metrics = JSON.parse(data);
        const line = `Metrics: in=${metrics.input_tokens} tok, out=${metrics.output_tokens} tok, chunks=${metrics.chunks_retrieved}`;
        this.pushProgress(line, "done");
      } catch {
        this.pushProgress(`Metrics: ${data}`, "done");
      }
      this.refresh();
      return;
    }
    if (eventName === "facts") {
      try {
        const facts = JSON.parse(data);
        const line = `Facts: read=${facts.files_read} files, bytes=${facts.context_bytes}, backend=${facts.backend}`;
        this.pushProgress(line, "done");
      } catch {
        this.pushProgress(`Facts: ${data}`, "done");
      }
      this.refresh();
      return;
    }
    if (eventName === "error") {
      this.messages.push({ role: "assistant", text: data, timestamp: Date.now() });
      this.refresh();
      return;
    }
  }

  public async runAction(action: string, payload: any = {}): Promise<void> {
    switch (action) {
      case "ping":
        await this.ping();
        break;
      case "interrupt":
        await this.interrupt();
        break;
      case "mcpOpenConfig":
        await this.openMcpConfig();
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
        if (payload?.id) {
          await this.snapshotRestoreById(payload.id);
        } else {
          await this.snapshotRestore();
        }
        break;
      case "addModel":
        await this.addModel(payload);
        break;
      case "removeModel":
        await this.removeModel(payload);
        break;
      case "saveInference":
        await this.saveInferenceConfig(payload);
        break;
    }
  }

  private async addModel(payload: any): Promise<void> {
    const res = await this.api.post<any>("/models/add", payload);
    if (!res.ok) {
      vscode.window.showErrorMessage(`Add model failed: ${res.error}`);
      return;
    }
    await this.loadModels();
  }

  private async removeModel(payload: any): Promise<void> {
    const res = await this.api.post<any>("/models/remove", payload);
    if (!res.ok) {
      vscode.window.showErrorMessage(`Remove model failed: ${res.error}`);
      return;
    }
    await this.loadModels();
  }

  private async interrupt(): Promise<void> {
    if (this.currentStreamReader) {
      try {
        await this.currentStreamReader.cancel();
      } catch {}
      this.currentStreamReader = null;
      this.messages.push({ role: "assistant", text: "Request interrupted.", timestamp: Date.now() });
      this.refresh();
    }
  }

  private async openMcpConfig(): Promise<void> {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) return;
    const path = vscode.Uri.joinPath(vscode.Uri.file(root), "configs", "mcp.yaml");
    try {
      const doc = await vscode.workspace.openTextDocument(path);
      await vscode.window.showTextDocument(doc, { preview: true });
    } catch {
      // ignore if missing
    }
  }

  private async ping(): Promise<void> {
    this.serverUrl = vscode.workspace.getConfiguration("localCodeAgent").get<string>("serverUrl", "");
    const res = await this.api.get<{ mcp_allowed: boolean }>("/mcp/status");
    if (res.ok) {
      this.status = "Connected";
      this.mcpStatusText = JSON.stringify(res.data, null, 2);
      await this.refreshIndexStatus();
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
    const depth = vscode.workspace.getConfiguration("localCodeAgent").get<"shallow" | "standard" | "deep">("infoSummaryDepth", "standard");
    if (sendContext) {
      this.pushProgress("Reading workspace files…", "running");
      const bundle = await gatherContext();
      payload.context = bundle;
      const workspaceContext = await gatherWorkspaceContext(depth);
      payload.workspace_context = workspaceContext;
      this.markProgressDone("Reading workspace files…");
    }
    const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("useStreaming", false);
    if (useStreaming) {
      const stream = await this.api.postStream("/propose_stream", payload);
      if (!stream.ok) {
        this.messages.push({ role: "assistant", text: `Propose failed: ${stream.error}`, timestamp: Date.now() });
        this.refresh();
        return;
      }
      let diff = "";
      let summary = "";
      let risk = "";
      const msgIndex = this.messages.length;
      this.currentStreamReader = stream.reader;
      this.messages.push({ role: "assistant", text: "Streaming proposal…\n", timestamp: Date.now(), streaming: true });
      this.refresh();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await stream.reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let eventName = "message";
          let data = "";
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              data += line.slice(5).trimStart() + "\n";
            }
          }
          data = data.replace(/\n$/, "");
          if (!data) continue;
          if (eventName === "summary") summary += data;
          if (eventName === "risk") risk += data;
          if (eventName === "diff") diff += data;
          const current = this.messages[msgIndex];
          if (current && current.role === "assistant") {
            current.text = `Summary:\n${summary || "(pending)"}\n\nRisk:\n${risk || "(pending)"}\n\nDiff:\n${diff.slice(0, 4000)}`;
          }
          this.refresh();
        }
      }
      const current = this.messages[msgIndex];
      if (current && current.role === "assistant") {
        current.streaming = false;
      }
      this.currentStreamReader = null;
      this.pending = { diff, summary, riskNotes: risk };
      this.messages.push({ role: "assistant", text: summary || "Proposal ready", timestamp: Date.now() });
      this.refresh();
      return;
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
    const depth = vscode.workspace.getConfiguration("localCodeAgent").get<"shallow" | "standard" | "deep">("infoSummaryDepth", "standard");
    if (sendContext) {
      this.pushProgress("Reading workspace files…", "running");
      const bundle = await gatherContext();
      payload.context = bundle;
      const workspaceContext = await gatherWorkspaceContext(depth);
      payload.workspace_context = workspaceContext;
      this.markProgressDone("Reading workspace files…");
    }
    const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("useStreaming", false);
    if (useStreaming) {
      const stream = await this.api.postStream("/revise_pending_stream", payload);
      if (!stream.ok) {
        this.messages.push({ role: "assistant", text: `Revise failed: ${stream.error}`, timestamp: Date.now() });
        this.refresh();
        return;
      }
      let diff = "";
      let summary = "";
      let risk = "";
      const msgIndex = this.messages.length;
      this.currentStreamReader = stream.reader;
      this.messages.push({ role: "assistant", text: "Streaming revision…\n", timestamp: Date.now(), streaming: true });
      this.refresh();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await stream.reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let eventName = "message";
          let data = "";
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              data += line.slice(5).trimStart() + "\n";
            }
          }
          data = data.replace(/\n$/, "");
          if (!data) continue;
          if (eventName === "summary") summary += data;
          if (eventName === "risk") risk += data;
          if (eventName === "diff") diff += data;
          const current = this.messages[msgIndex];
          if (current && current.role === "assistant") {
            current.text = `Summary:\n${summary || "(pending)"}\n\nRisk:\n${risk || "(pending)"}\n\nDiff:\n${diff.slice(0, 4000)}`;
          }
          this.refresh();
        }
      }
      const current = this.messages[msgIndex];
      if (current && current.role === "assistant") {
        current.streaming = false;
      }
      this.currentStreamReader = null;
      this.pending = { diff, summary, riskNotes: risk };
      this.messages.push({ role: "assistant", text: summary || "Revised pending patch", timestamp: Date.now() });
      this.refresh();
      return;
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
    const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get<boolean>("useStreaming", false);
    if (useStreaming) {
      const stream = await this.api.postStream("/approve_stream", { unified_diff: diffToApply });
      if (!stream.ok) {
        this.messages.push({ role: "assistant", text: `Approve failed: ${stream.error}`, timestamp: Date.now() });
        this.refresh();
        return;
      }
      const msgIndex = this.messages.length;
      this.currentStreamReader = stream.reader;
      this.messages.push({ role: "assistant", text: "Approving…\n", timestamp: Date.now(), streaming: true });
      this.refresh();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await stream.reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let eventName = "message";
          let data = "";
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              data += line.slice(5).trimStart() + "\n";
            }
          }
          data = data.replace(/\n$/, "");
          if (!data) continue;
          const current = this.messages[msgIndex];
          if (current && current.role === "assistant") {
            current.text += `${data}\n`;
          }
          this.refresh();
        }
      }
      const current = this.messages[msgIndex];
      if (current && current.role === "assistant") {
        current.streaming = false;
      }
      this.currentStreamReader = null;
      this.pending = null;
      await this.api.post<any>("/reset_context", {});
      this.refresh();
      return;
    }
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

  private async snapshotRestoreById(id: string): Promise<void> {
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

  private async loadInferenceConfig(): Promise<void> {
    const res = await this.api.get<any>("/inference/config");
    if (!res.ok) {
      return;
    }
    this.inferenceConfig = res.data;
  }

  private async saveInferenceConfig(payload: any): Promise<void> {
    const res = await this.api.post<any>("/inference/config", payload);
    if (!res.ok) {
      vscode.window.showErrorMessage(`Update inference config failed: ${res.error}`);
      return;
    }
    await this.loadInferenceConfig();
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
        indexStatus: this.indexStatus,
        repoRoot: this.repoRoot,
        repoRootStateless: this.repoRootStateless,
        repoRootRequested: this.repoRootRequested,
        inferenceConfig: this.inferenceConfig,
      });
    }
  }

  private pushProgress(text: string, status: "running" | "done" | "error"): void {
    this.progress.push({ text, status });
    if (this.progress.length > 12) {
      this.progress = this.progress.slice(-12);
    }
  }

  private markProgressDone(text: string): void {
    const item = this.progress.find((p) => p.text === text);
    if (item) item.status = "done";
  }

  private markProgressError(text: string): void {
    const item = this.progress.find((p) => p.text === text);
    if (item) item.status = "error";
  }

  private startIndexPolling(): void {
    if (this.indexPoller) return;
    this.indexPoller = setInterval(() => {
      this.refreshIndexStatus();
    }, 5000);
    this.context.subscriptions.push({ dispose: () => this.stopIndexPolling() });
  }

  private stopIndexPolling(): void {
    if (this.indexPoller) {
      clearInterval(this.indexPoller);
      this.indexPoller = null;
    }
  }

  private async refreshIndexStatus(): Promise<void> {
    const res = await this.api.get<any>(`/index/status?after_id=${this.lastIndexEventId}`);
    if (!res.ok) {
      return;
    }
    this.indexStatus = res.data;
    const events = res.data.events || [];
    for (const evt of events) {
      const text = evt?.text ? `Indexer: ${evt.text}` : "";
      if (text) {
        this.pushProgress(text, "running");
      }
      if (evt?.id && evt.id > this.lastIndexEventId) {
        this.lastIndexEventId = evt.id;
      }
    }
    if (this.indexStatus?.last_error) {
      this.pushProgress(`Indexer error: ${this.indexStatus.last_error}`, "error");
    }
    this.refresh();
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
