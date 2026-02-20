Problem summary (from fix6)
- The server is indexing an empty repo_root in the VM (`/workspace/.../.agent_stateless/default`). It cannot see your local VSCode workspace path. So INFO answers read “Empty repository.”
- This is not a planner bug; it’s a missing context/data path between local VSCode and VM server.

Likely root cause
- Server runs in VM and uses its own filesystem. VS Code UI is local. `/init` is being called with a VM-local path, not the local workspace. No context bundle is being sent, so INFO pipeline has nothing real to read.

Mitigation plan (context bundle)
1. Add local context bundle from VS Code extension to every `/query` and `/propose`:
   - workspace name
   - top-level tree (limited)
   - README, package.json, pyproject.toml, Makefile, docker-compose, vite/next configs
   - scripts extracted from package.json
2. Extend server request models to accept `context` and prefer it for INFO answers (and optionally for repo map summary) instead of reading VM filesystem.
3. If context is missing and repo_root points to `.agent_stateless/default`, return a clear error message telling the user to enable context bundle or init with a real VM path.
4. Keep MCP ungated and existing flows intact; only enhance INFO and context ingestion.

Mitigation plan (streaming + chunking)
1. Add a streaming response path for `/query` (SSE or chunked HTTP) and update the webview to render partial text as it arrives.
2. Add a fallback to non‑streaming for clients that don’t support streaming.
3. Add an output‑size guard: if response is large, split into sections and stream them incrementally.
4. If an LLM cannot return a large answer in one pass, use the task system to request the next part (e.g., “continue with section X”), then append in the UI.

Answer streaming question
- Today the UI shows the full response only after the server returns (no streaming). It will be a single message.
- If you want GPT‑style streaming, we need SSE/WebSocket or chunked response handling plus UI streaming render support.
