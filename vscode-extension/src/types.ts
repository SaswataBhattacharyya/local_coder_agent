export type ChatMessage = {
  role: "user" | "assistant" | "system";
  text: string;
  timestamp: number;
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
