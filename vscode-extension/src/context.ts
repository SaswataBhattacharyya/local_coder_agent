import * as vscode from "vscode";
import { ContextBundle, WorkspaceContextBundle } from "./types";

export async function gatherContext(): Promise<ContextBundle> {
  const bundle: ContextBundle = { files: [], snippets: [] };
  const editor = vscode.window.activeTextEditor;
  if (editor) {
    const doc = editor.document;
    const selection = editor.selection;
    let text = "";
    if (!selection.isEmpty) {
      text = doc.getText(selection);
    } else {
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
    const tasks: Array<Thenable<void>> = [];
    const ws: any = vscode.workspace as any;
    await ws.findTextInFiles({ pattern: query }, { maxResults: 20 }, (result: any) => {
      const docUri = result.uri;
      const start = Math.max(0, result.ranges[0].start.line - 4);
      const end = result.ranges[0].end.line + 4;
      const range = new vscode.Range(start, 0, end, 0);
      const t = vscode.workspace.openTextDocument(docUri).then((doc: vscode.TextDocument) => {
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
const MAX_TREE_ENTRIES = 300;
const MAX_EXTRA_FILES = 30;

export async function gatherWorkspaceContext(
  depth: "shallow" | "standard" | "deep" = "standard",
): Promise<WorkspaceContextBundle | null> {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!root) {
    return null;
  }
  const workspaceName = vscode.workspace.workspaceFolders?.[0]?.name || "workspace";
  const tree: { name: string; type: "file" | "dir" }[] = [];
  try {
    const entries = await vscode.workspace.fs.readDirectory(root);
    for (const [name, type] of entries.slice(0, MAX_TREE_ENTRIES)) {
      tree.push({ name, type: type === vscode.FileType.Directory ? "dir" : "file" });
    }
    if (entries.find(([name, type]) => name === "src" && type === vscode.FileType.Directory)) {
      const srcDir = vscode.Uri.joinPath(root, "src");
      const srcEntries = await vscode.workspace.fs.readDirectory(srcDir);
      for (const [name, type] of srcEntries.slice(0, MAX_TREE_ENTRIES)) {
        const rel = `src/${name}`;
        tree.push({ name: rel, type: type === vscode.FileType.Directory ? "dir" : "file" });
      }
    }
  } catch {
    // ignore tree errors
  }

  const files: { path: string; content: string }[] = [];
  const toRead: vscode.Uri[] = [];
  const rootPath = root.fsPath;

  // README*
  try {
    const entries = await vscode.workspace.fs.readDirectory(root);
    for (const [name, type] of entries) {
      if (type !== vscode.FileType.File) continue;
      if (/^README(\\.[A-Za-z0-9]+)?$/i.test(name)) {
        toRead.push(vscode.Uri.joinPath(root, name));
        break;
      }
    }
  } catch {
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
    "index.html",
  ];
  for (const name of fixedFiles) {
    toRead.push(vscode.Uri.joinPath(root, name));
  }

  const configGlobs = ["vite.config.", "next.config.", "tailwind.config.", "eslint.config.", "vitest.config.", "tsconfig."];
  try {
    const entries = await vscode.workspace.fs.readDirectory(root);
    for (const [name, type] of entries) {
      if (type !== vscode.FileType.File) continue;
      if (configGlobs.some((p) => name.startsWith(p))) {
        toRead.push(vscode.Uri.joinPath(root, name));
      }
      if (name.endsWith(".lock") || name === "package-lock.json" || name === "pnpm-lock.yaml" || name === "yarn.lock" || name === "bun.lockb") {
        toRead.push(vscode.Uri.joinPath(root, name));
      }
    }
  } catch {
    // ignore
  }

  if (depth !== "shallow") {
    const srcCandidates = [
      "src/main.tsx",
      "src/main.ts",
      "src/index.tsx",
      "src/index.ts",
      "src/App.tsx",
      "src/App.ts",
      "src/app.tsx",
      "src/app.ts",
    ];
    for (const rel of srcCandidates) {
      toRead.push(vscode.Uri.joinPath(root, rel));
    }
    const extraDirs = ["src/pages", "src/routes", "src/components"];
    for (const d of extraDirs) {
      try {
        const dir = vscode.Uri.joinPath(root, d);
        const entries = await vscode.workspace.fs.readDirectory(dir);
        for (const [name, type] of entries.slice(0, MAX_EXTRA_FILES)) {
          if (type !== vscode.FileType.File) continue;
          toRead.push(vscode.Uri.joinPath(dir, name));
        }
      } catch {
        // ignore
      }
    }
  }

  if (depth === "deep") {
    try {
      const srcDir = vscode.Uri.joinPath(root, "src");
      const entries = await vscode.workspace.fs.readDirectory(srcDir);
      let count = 0;
      for (const [name, type] of entries) {
        if (type !== vscode.FileType.File) continue;
        toRead.push(vscode.Uri.joinPath(srcDir, name));
        count += 1;
        if (count >= MAX_EXTRA_FILES) break;
      }
    } catch {
      // ignore
    }
  }

  const scripts: Record<string, string> = {};
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
        } catch {
          // ignore JSON parse errors
        }
      }
    } catch {
      // ignore missing files
    }
  }

  return {
    workspaceName,
    rootPath,
    tree,
    files,
    packageScripts: Object.keys(scripts).length ? scripts : undefined,
    gatherMode: depth,
  };
}
