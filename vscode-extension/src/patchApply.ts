import * as vscode from "vscode";
import { spawn } from "child_process";
import * as path from "path";
import * as fs from "fs";

export async function applyPatch(diffText: string): Promise<{ ok: boolean; message: string }>{
  const cfg = vscode.workspace.getConfiguration("localCodeAgent");
  const useGitApply = cfg.get<boolean>("useGitApply", true);
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!root) {
    return { ok: false, message: "No workspace folder open" };
  }

  if (useGitApply && fs.existsSync(path.join(root, ".git"))) {
    const gitResult = await gitApply(root, diffText);
    if (gitResult.ok) {
      return gitResult;
    }
  }

  try {
    await applyUnifiedDiff(root, diffText);
    return { ok: true, message: "Patch applied (WorkspaceEdit)" };
  } catch (err: any) {
    return { ok: false, message: err?.message || "Failed to apply patch" };
  }
}

function gitApply(cwd: string, diffText: string): Promise<{ ok: boolean; message: string }>{
  return new Promise((resolve) => {
    const child = spawn("git", ["apply", "--whitespace=nowarn", "-"], { cwd });
    let stderr = "";
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("close", (code) => {
      if (code === 0) {
        resolve({ ok: true, message: "Patch applied (git apply)" });
      } else {
        resolve({ ok: false, message: stderr || "git apply failed" });
      }
    });
    child.stdin.write(diffText);
    child.stdin.end();
  });
}

async function applyUnifiedDiff(root: string, diffText: string): Promise<void> {
  const files = parseDiff(diffText);
  for (const file of files) {
    const filePath = path.join(root, file.path);
    const exists = fs.existsSync(filePath);
    const original = exists ? fs.readFileSync(filePath, "utf8") : "";
    const updated = applyHunks(original, file.hunks);
    const uri = vscode.Uri.file(filePath);
    const edit = new vscode.WorkspaceEdit();
    if (!exists) {
      edit.createFile(uri, { overwrite: true });
    }
    const fullRange = new vscode.Range(0, 0, Math.max(original.split("\n").length, 1), 0);
    edit.replace(uri, fullRange, updated);
    const ok = await vscode.workspace.applyEdit(edit);
    if (!ok) {
      throw new Error(`Failed to apply edits to ${file.path}`);
    }
  }
}

type DiffFile = { path: string; hunks: Hunk[] };

type Hunk = {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: string[];
};

function parseDiff(diffText: string): DiffFile[] {
  const lines = diffText.split(/\r?\n/);
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("diff --git")) {
      current = null;
      let j = i + 1;
      while (j < lines.length && !lines[j].startsWith("diff --git")) {
        if (lines[j].startsWith("+++ b/")) {
          const pathPart = lines[j].replace("+++ b/", "").trim();
          current = { path: pathPart, hunks: [] };
          files.push(current);
          break;
        }
        j++;
      }
      i++;
      continue;
    }
    if (line.startsWith("@@") && current) {
      const hunk = parseHunkHeader(line);
      i++;
      while (i < lines.length && !lines[i].startsWith("@@") && !lines[i].startsWith("diff --git")) {
        hunk.lines.push(lines[i]);
        i++;
      }
      current.hunks.push(hunk);
      continue;
    }
    i++;
  }
  return files;
}

function parseHunkHeader(line: string): Hunk {
  const match = /@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
  if (!match) {
    return { oldStart: 0, oldLines: 0, newStart: 0, newLines: 0, lines: [] };
  }
  return {
    oldStart: parseInt(match[1], 10),
    oldLines: parseInt(match[2] || "1", 10),
    newStart: parseInt(match[3], 10),
    newLines: parseInt(match[4] || "1", 10),
    lines: [],
  };
}

function applyHunks(original: string, hunks: Hunk[]): string {
  let lines = original.split("\n");
  let offset = 0;
  for (const hunk of hunks) {
    let idx = hunk.oldStart - 1 + offset;
    const newLines: string[] = [];
    for (const l of hunk.lines) {
      if (l.startsWith("+")) {
        newLines.push(l.slice(1));
      } else if (l.startsWith("-")) {
        idx++;
      } else if (l.startsWith(" ")) {
        newLines.push(l.slice(1));
        idx++;
      }
    }
    const before = lines.slice(0, hunk.oldStart - 1 + offset);
    const after = lines.slice(hunk.oldStart - 1 + offset + hunk.oldLines);
    lines = before.concat(newLines, after);
    offset += newLines.length - hunk.oldLines;
  }
  return lines.join("\n");
}
