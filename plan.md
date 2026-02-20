What I think the problem is
- The server returns 200 OK for `/query`, the UI shows “Plan ready”, but nothing is rendered. This almost always means the webview isn’t rendering the response, or the response schema doesn’t match what the UI expects. It is not an LLM hang.
- In this repo, `/query` currently returns `state`, `questions`, `plan`, and (now) `answer` for INFO. The VS Code extension only uses `questions` and ignores `plan` and `answer`, so INFO answers won’t render unless the webview/extension is updated.
- If the UI is waiting for a second step (like `/propose`) after a plan, it will never show an answer for INFO unless it auto-chains to a response.
- Any contract drift (missing/renamed fields) between server and extension will produce the “Plan ready, no output” symptom.

How to mitigate
- Ensure `/query` returns a consistent schema and the UI renders it:
  - If INFO: show `answer` immediately.
  - If NEEDS_INFO: show `questions`.
  - If EDIT/COMMAND: show `plan` (or ask for confirmation).
- Update the extension handler to append `answer` to messages if present. Right now it only appends `questions`.
- Add minimal debugging logs in the extension output channel to print status code + truncated JSON to see mismatches instantly.
- Optionally add a trace_id and step timings in server responses to make request flow visible.

What this means for the current codebase
- The server-side INFO answer exists now, but the UI is not wired to display it. That is the likely source of the “Plan ready” with no message.
- This is isolated to planning/execution response handling; init and UI setup code should remain unchanged.

Next steps if you want me to fix it
1. Update the VS Code extension (`vscode-extension/src/extension.ts`) to:
   - render `answer` when present
   - log response status + JSON in Output channel
2. Add a simple response schema guard so unknown responses surface an error bubble.
3. (Optional) add trace_id + timing spans in server responses for debugging.
