export type ChatMessage = {
  role: "user" | "assistant" | "system";
  text: string;
  timestamp: number;
  streaming?: boolean;
};

export type PendingPatch = {
  diff: string;
  summary: string;
  riskNotes: string;
};

export type ContextBundle = {
  files: { path: string; content: string }[];
  snippets: { path: string; startLine: number; endLine: number; text: string }[];
};

export type WorkspaceContextBundle = {
  workspaceName: string;
  rootPath: string;
  tree: { name: string; type: "file" | "dir" }[];
  files: { path: string; content: string }[];
  packageScripts?: Record<string, string>;
};
