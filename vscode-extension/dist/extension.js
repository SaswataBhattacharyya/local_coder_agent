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
        this.output = vscode.window.createOutputChannel("Local Code Agent");
        this.api = new apiClient_1.ApiClient((msg) => this.output.appendLine(msg));
        this.messages = [];
        this.progress = [];
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
        view.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "dist-webview")],
        };
        view.webview.html = this.getHtml(view.webview);
        view.webview.onDidReceiveMessage(async (msg) => {
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
        const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get("useStreaming", false);
        if (useStreaming) {
            await this.handleUserMessageStream(text);
            return;
        }
        this.messages.push({ role: "user", text, timestamp: Date.now() });
        this.progress = [];
        this.pushProgress("Connecting to server…", "running");
        this.refresh();
        await this.ensureInit();
        this.markProgressDone("Connecting to server…");
        this.pushProgress("Planning request…", "running");
        const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get("sendContextBundle", false);
        const workspaceContext = sendContext ? await (0, context_1.gatherWorkspaceContext)() : null;
        const res = await this.api.post("/query", { user_text: text, workspace_context: workspaceContext });
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
    async handleUserMessageStream(text) {
        this.messages.push({ role: "user", text, timestamp: Date.now() });
        this.progress = [];
        this.pushProgress("Connecting to server…", "running");
        this.refresh();
        await this.ensureInit();
        this.markProgressDone("Connecting to server…");
        this.pushProgress("Streaming response…", "running");
        const sendContext = vscode.workspace.getConfiguration("localCodeAgent").get("sendContextBundle", false);
        const workspaceContext = sendContext ? await (0, context_1.gatherWorkspaceContext)() : null;
        const res = await this.api.postStream("/query_stream", { user_text: text, workspace_context: workspaceContext });
        if (!res.ok) {
            this.markProgressError("Streaming response…");
            this.messages.push({ role: "assistant", text: `Query failed: ${res.error}`, timestamp: Date.now() });
            this.refresh();
            return;
        }
        const msgIndex = this.messages.length;
        this.messages.push({ role: "assistant", text: "", timestamp: Date.now(), streaming: true });
        this.refresh();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        while (true) {
            const { value, done } = await res.reader.read();
            if (done)
                break;
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
        this.markProgressDone("Streaming response…");
        this.refresh();
    }
    handleSseEvent(raw, msgIndex) {
        let eventName = "message";
        let data = "";
        for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) {
                eventName = line.slice(6).trim();
            }
            else if (line.startsWith("data:")) {
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
        if (eventName === "error") {
            this.messages.push({ role: "assistant", text: data, timestamp: Date.now() });
            this.refresh();
            return;
        }
    }
    async runAction(action, payload = {}) {
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
            const workspaceContext = await (0, context_1.gatherWorkspaceContext)();
            payload.workspace_context = workspaceContext;
        }
        const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get("useStreaming", false);
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
            this.messages.push({ role: "assistant", text: "Streaming proposal…\n", timestamp: Date.now(), streaming: true });
            this.refresh();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";
            while (true) {
                const { value, done } = await stream.reader.read();
                if (done)
                    break;
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
                        }
                        else if (line.startsWith("data:")) {
                            data += line.slice(5).trimStart() + "\n";
                        }
                    }
                    data = data.replace(/\n$/, "");
                    if (!data)
                        continue;
                    if (eventName === "summary")
                        summary += data;
                    if (eventName === "risk")
                        risk += data;
                    if (eventName === "diff")
                        diff += data;
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
            this.pending = { diff, summary, riskNotes: risk };
            this.messages.push({ role: "assistant", text: summary || "Proposal ready", timestamp: Date.now() });
            this.refresh();
            return;
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
            const workspaceContext = await (0, context_1.gatherWorkspaceContext)();
            payload.workspace_context = workspaceContext;
        }
        const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get("useStreaming", false);
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
            this.messages.push({ role: "assistant", text: "Streaming revision…\n", timestamp: Date.now(), streaming: true });
            this.refresh();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";
            while (true) {
                const { value, done } = await stream.reader.read();
                if (done)
                    break;
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
                        }
                        else if (line.startsWith("data:")) {
                            data += line.slice(5).trimStart() + "\n";
                        }
                    }
                    data = data.replace(/\n$/, "");
                    if (!data)
                        continue;
                    if (eventName === "summary")
                        summary += data;
                    if (eventName === "risk")
                        risk += data;
                    if (eventName === "diff")
                        diff += data;
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
            this.pending = { diff, summary, riskNotes: risk };
            this.messages.push({ role: "assistant", text: summary || "Revised pending patch", timestamp: Date.now() });
            this.refresh();
            return;
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
    async approve(diffOverride) {
        if (!this.pending) {
            vscode.window.showWarningMessage("No pending patch");
            return;
        }
        const diffToApply = diffOverride || this.pending.diff;
        const useStreaming = vscode.workspace.getConfiguration("localCodeAgent").get("useStreaming", false);
        if (useStreaming) {
            const stream = await this.api.postStream("/approve_stream", { unified_diff: diffToApply });
            if (!stream.ok) {
                this.messages.push({ role: "assistant", text: `Approve failed: ${stream.error}`, timestamp: Date.now() });
                this.refresh();
                return;
            }
            const msgIndex = this.messages.length;
            this.messages.push({ role: "assistant", text: "Approving…\n", timestamp: Date.now(), streaming: true });
            this.refresh();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";
            while (true) {
                const { value, done } = await stream.reader.read();
                if (done)
                    break;
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
                        }
                        else if (line.startsWith("data:")) {
                            data += line.slice(5).trimStart() + "\n";
                        }
                    }
                    data = data.replace(/\n$/, "");
                    if (!data)
                        continue;
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
            this.pending = null;
            await this.api.post("/reset_context", {});
            this.refresh();
            return;
        }
        // Prefer server-side approve (works for VM + local)
        const serverRes = await this.api.post("/approve", { unified_diff: diffToApply });
        if (serverRes.ok) {
            this.messages.push({ role: "assistant", text: "Approved on server", timestamp: Date.now() });
            this.pending = null;
            await this.api.post("/reset_context", {});
            this.refresh();
            return;
        }
        // Fallback to local apply
        const res = await (0, patchApply_1.applyPatch)(diffToApply);
        if (!res.ok) {
            vscode.window.showErrorMessage(res.message);
            await this.openDiffPreview(diffToApply);
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
                progress: this.progress,
            });
        }
    }
    pushProgress(text, status) {
        this.progress.push({ text, status });
    }
    markProgressDone(text) {
        const item = this.progress.find((p) => p.text === text);
        if (item)
            item.status = "done";
    }
    markProgressError(text) {
        const item = this.progress.find((p) => p.text === text);
        if (item)
            item.status = "error";
    }
    getHtml(webview) {
        const nonce = getNonce();
        const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist-webview", "index.js"));
        const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "dist-webview", "index.css"));
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
