export type DiffLine = {
  type: "add" | "del" | "ctx" | "meta";
  text: string;
  oldLine?: number;
  newLine?: number;
  accepted?: boolean;
};

export type DiffHunk = {
  header: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLine[];
};

export type DiffFile = {
  header: string[];
  path: string;
  hunks: DiffHunk[];
};

const HUNK_RE = /^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@/;

export function parseUnifiedDiff(diff: string): DiffFile[] {
  const lines = diff.split("\n");
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let currentHunk: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      if (current) {
        files.push(current);
      }
      current = { header: [line], path: "", hunks: [] };
      currentHunk = null;
      continue;
    }
    if (!current) {
      continue;
    }
    if (line.startsWith("index ") || line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("new file") || line.startsWith("deleted file")) {
      current.header.push(line);
      if (line.startsWith("+++ b/")) {
        current.path = line.slice(6).trim();
      }
      continue;
    }
    const hunkMatch = line.match(HUNK_RE);
    if (hunkMatch) {
      const oldStart = parseInt(hunkMatch[1], 10);
      const oldLines = hunkMatch[2] ? parseInt(hunkMatch[2], 10) : 1;
      const newStart = parseInt(hunkMatch[3], 10);
      const newLines = hunkMatch[4] ? parseInt(hunkMatch[4], 10) : 1;
      oldLine = oldStart;
      newLine = newStart;
      currentHunk = { header: line, oldStart, oldLines, newStart, newLines, lines: [] };
      current.hunks.push(currentHunk);
      continue;
    }
    if (!currentHunk) {
      current.header.push(line);
      continue;
    }
    if (line.startsWith("+")) {
      currentHunk.lines.push({ type: "add", text: line, newLine, accepted: true });
      newLine += 1;
    } else if (line.startsWith("-")) {
      currentHunk.lines.push({ type: "del", text: line, oldLine, accepted: true });
      oldLine += 1;
    } else if (line.startsWith(" ")) {
      currentHunk.lines.push({ type: "ctx", text: line, oldLine, newLine });
      oldLine += 1;
      newLine += 1;
    } else {
      currentHunk.lines.push({ type: "meta", text: line });
    }
  }
  if (current) {
    files.push(current);
  }
  return files;
}

export function buildUnifiedDiff(files: DiffFile[]): string {
  const out: string[] = [];
  for (const file of files) {
    const hunks = file.hunks
      .map((h) => {
        const kept = h.lines.filter((l) => {
          if (l.type === "add" || l.type === "del") return l.accepted !== false;
          return true;
        });
        const hasChanges = kept.some((l) => l.type === "add" || l.type === "del");
        if (!hasChanges) return null;
        const oldCount = kept.filter((l) => l.type === "ctx" || l.type === "del").length;
        const newCount = kept.filter((l) => l.type === "ctx" || l.type === "add").length;
        const header = `@@ -${h.oldStart},${oldCount} +${h.newStart},${newCount} @@`;
        return { header, lines: kept };
      })
      .filter(Boolean) as { header: string; lines: DiffLine[] }[];

    if (hunks.length === 0) {
      continue;
    }
    out.push(...file.header);
    for (const h of hunks) {
      out.push(h.header);
      for (const l of h.lines) {
        out.push(l.text);
      }
    }
  }
  return out.join("\n");
}
