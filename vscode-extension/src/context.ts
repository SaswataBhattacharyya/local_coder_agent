import * as vscode from "vscode";
import { ContextBundle } from "./types";

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
    const tasks: Promise<void>[] = [];
    await vscode.workspace.findTextInFiles({ pattern: query }, { maxResults: 20 }, (result: vscode.TextSearchResult) => {
      const docUri = result.uri;
      const start = Math.max(0, result.ranges[0].start.line - 4);
      const end = result.ranges[0].end.line + 4;
      const range = new vscode.Range(start, 0, end, 0);
      tasks.push(
        vscode.workspace.openTextDocument(docUri).then((doc: vscode.TextDocument) => {
          const text = doc.getText(range);
          bundle.snippets.push({
            path: docUri.fsPath,
            startLine: start + 1,
            endLine: end + 1,
            text,
          });
        })
      );
    });
    await Promise.all(tasks);
  }

  return bundle;
}
