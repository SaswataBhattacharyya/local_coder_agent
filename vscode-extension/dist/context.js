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
exports.gatherContext = gatherContext;
exports.gatherWorkspaceContext = gatherWorkspaceContext;
const vscode = __importStar(require("vscode"));
async function gatherContext() {
    const bundle = { files: [], snippets: [] };
    const editor = vscode.window.activeTextEditor;
    if (editor) {
        const doc = editor.document;
        const selection = editor.selection;
        let text = "";
        if (!selection.isEmpty) {
            text = doc.getText(selection);
        }
        else {
            const maxLines = Math.min(doc.lineCount, 200);
            const range = new vscode.Range(0, 0, maxLines, 0);
            text = doc.getText(range);
        }
        bundle.files.push({ path: doc.uri.fsPath, content: text });
    }
    const query = await vscode.window.showInputBox({
        prompt: "Optional: enter a search term to include snippets",
        placeHolder: "e.g. function name or keyword",
    });
    if (query) {
        const tasks = [];
        const ws = vscode.workspace;
        await ws.findTextInFiles({ pattern: query }, { maxResults: 20 }, (result) => {
            const docUri = result.uri;
            const start = Math.max(0, result.ranges[0].start.line - 4);
            const end = result.ranges[0].end.line + 4;
            const range = new vscode.Range(start, 0, end, 0);
            const t = vscode.workspace.openTextDocument(docUri).then((doc) => {
                const text = doc.getText(range);
                bundle.snippets.push({
                    path: docUri.fsPath,
                    startLine: start + 1,
                    endLine: end + 1,
                    text,
                });
            });
            tasks.push(t);
        });
        await Promise.all(tasks.map((t) => Promise.resolve(t)));
    }
    return bundle;
}
const MAX_FILE_CHARS = 12000;
const MAX_TREE_ENTRIES = 200;
async function gatherWorkspaceContext() {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!root) {
        return null;
    }
    const workspaceName = vscode.workspace.workspaceFolders?.[0]?.name || "workspace";
    const tree = [];
    try {
        const entries = await vscode.workspace.fs.readDirectory(root);
        for (const [name, type] of entries.slice(0, MAX_TREE_ENTRIES)) {
            tree.push({ name, type: type === vscode.FileType.Directory ? "dir" : "file" });
        }
    }
    catch {
        // ignore tree errors
    }
    const files = [];
    const toRead = [];
    const rootPath = root.fsPath;
    // README*
    try {
        const entries = await vscode.workspace.fs.readDirectory(root);
        for (const [name, type] of entries) {
            if (type !== vscode.FileType.File)
                continue;
            if (/^README(\\.[A-Za-z0-9]+)?$/i.test(name)) {
                toRead.push(vscode.Uri.joinPath(root, name));
                break;
            }
        }
    }
    catch {
        // ignore
    }
    const fixedFiles = [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Makefile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".env.example",
    ];
    for (const name of fixedFiles) {
        toRead.push(vscode.Uri.joinPath(root, name));
    }
    const configGlobs = ["vite.config.", "next.config."];
    try {
        const entries = await vscode.workspace.fs.readDirectory(root);
        for (const [name, type] of entries) {
            if (type !== vscode.FileType.File)
                continue;
            if (configGlobs.some((p) => name.startsWith(p))) {
                toRead.push(vscode.Uri.joinPath(root, name));
            }
        }
    }
    catch {
        // ignore
    }
    const scripts = {};
    for (const uri of toRead) {
        try {
            const data = await vscode.workspace.fs.readFile(uri);
            const text = new TextDecoder("utf-8").decode(data).slice(0, MAX_FILE_CHARS);
            files.push({ path: uri.fsPath, content: text });
            if (uri.path.endsWith("package.json")) {
                try {
                    const pkg = JSON.parse(text);
                    if (pkg && typeof pkg.scripts === "object") {
                        Object.assign(scripts, pkg.scripts);
                    }
                }
                catch {
                    // ignore JSON parse errors
                }
            }
        }
        catch {
            // ignore missing files
        }
    }
    return {
        workspaceName,
        rootPath,
        tree,
        files,
        packageScripts: Object.keys(scripts).length ? scripts : undefined,
    };
}
