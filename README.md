# Local Code Agent (MVP scaffold)

This is a **bootstrappable** local coding agent scaffold designed for VM installs.

## Quick start (Local)

```bash
python3 bootstrap.py
```

The bootstrap script will:
- create a local `venv/`
- install python deps
- ask for GPU VRAM (or auto-detect)
- snapshots are local-only and stored under `.agent/snapshots` (last 3 kept)
- download GGUF models (reasoner, coder, optional VLM)
- start the local FastAPI server

Note: If you change code or install new Python deps, restart the server. For dev, you can run:
```bash
uvicorn server.app:app --host 0.0.0.0 --port 8010 --reload
```

One-shot local (server + VSCode UI):
```bash
./scripts/start_local.sh
```

## Restore Remote (Optional)
- Snapshot endpoints:
  - `GET /snapshots`
  - `POST /snapshots/create`
  - `POST /snapshots/restore`
  - Optional `push_on_approve: true|false` toggles automatic push on approval.

Then set VSCode setting:
- `localCodeAgent.serverUrl` = `http://127.0.0.1:8010`

## VM/Runpod setup (Public endpoint vs SSH tunnel)
See `docs/SETUP.md` for the step-by-step interactive guide, including:
- Public endpoint (expose port 8010)
- SSH tunnel (keep serverUrl as `http://127.0.0.1:8010`)

## VSCode Extension
The extension lives in `vscode-extension/` and provides a right sidebar agent UI.
Install (dev):
```bash
cd vscode-extension
npm install
npm run build
```
Then press `F5` in VSCode to launch the Extension Development Host.
Optional: package as VSIX:
```bash
npm run package
```

Note: VSIX packaging via `npm run package` requires Node 20+ because `@vscode/vsce` pulls `undici`.

Helper:
```bash
python3 scripts/print_setup_steps.py
```

UI launch (local):
```bash
./scripts/start_ui.sh
```
Override server URL:
```bash
LOCAL_CODE_AGENT_SERVER_URL=https://<runpod-url> ./scripts/start_ui.sh
```

What `start_ui.sh` does:
1. Ensures VSCode CLI is available (`code`).
2. Writes `.vscode/settings.json` with `localCodeAgent.serverUrl`.
3. Installs/builds extension dependencies.
4. Opens VSCode with the extension in development mode.
5. Pings the server.

In the extension UI you can:
- Select Reasoner/Coder models (dropdowns)
- Configure Restore Remote
- Allow/Revoke MCP and reload MCP config

## VSCode Extension Build Notes
- Requires Node.js >= 18 for dev build
- If npm shows EBADENGINE for undici, upgrade Node to 20 or use VSCode's extension dev host with bundled Node

## Notes

- Shell commands are **gated**: the server will require explicit confirmation tokens.
- Edits are **staged** as pending patches until approved (snapshot created on approval).

## MCP (Optional)

An optional MCP bridge is available (including a free browser MCP via Playwright).
See `mcp/README.md` for setup and usage.

You can also drop a `mcp.json` in the repo root (see `mcp.json.example`) to add MCP servers without code changes. After editing, call `/mcp/reload`.

## Docs
- `docs/SETUP.md` (Local vs VM/Runpod setup)
- `docs/TEST_CHECKLIST.md` (manual test checklist)

## Models & Keys

- Local GGUF models are configured in `configs/config.yaml` under `model_registry`.
  Example:
  ```yaml
  model_registry:
    reasoner:
      default: deepseek-r1-distill-qwen-7b
      options:
        - id: deepseek-r1-distill-qwen-7b
          provider: local
          role: reasoner
          model_dir: reasoner
          filename_hint: Q4_K_M
          context: 8192
    coder:
      default: qwen2.5-coder-7b
      options:
        - id: qwen2.5-coder-7b
          provider: local
          role: coder
          model_dir: coder
          filename_hint: Q4_K_M
          context: 8192
  ```
- Cloud providers:
  - Add keys in `keys.env` (copy `keys.env.example`).
  - `OPENAI_API_KEY` and `GEMINI_API_KEY` will enable additional options.
- Defaults (when keys present): `gpt-4o-mini` (OpenAI) and `gemini-2.5-flash` (Gemini).
- Do not commit `keys.env`.
- In VSCode, use the dropdowns **Reasoner** and **Coder** to switch models.
- Default selection is **Best** (DeepSeek distill for reasoning, Qwen coder for coding).
- Selections persist in `configs/config.yaml` under `model_registry.selected` (global for this server instance).

## Context Ingestion (Large Inputs)

If large context is sent (from VSCode or MCP), the server chunks it and keeps the most relevant pieces.
Settings in `configs/config.yaml`:
```yaml
context_ingest:
  enabled: true
  max_chars: 12000
  chunk_size: 2000
  chunk_overlap: 200
  top_k: 6
```

## Agent State Branching

Agent state is stored under `.agent/state/` and is separate from snapshots.
Each session has branches with its own pending patch and notes.

Layout:
```
.agent/state/sessions/<session_id>/active_branch.txt
.agent/state/sessions/<session_id>/branches/<branch>/pending_patch.json
.agent/state/sessions/<session_id>/branches/<branch>/repo_map/
```

## Background Tasks

Durable queue stored under:
```
.agent/tasks/tasks.jsonl
.agent/tasks/<task_id>/{meta.json,logs.jsonl,result.json}
```
Worker status:
```
GET /worker/status
```
Includes: `started_at`, `thread_id`, `current_task`, `last_error`.
Worker mode (optional):
```
python -m server.worker --repo /path/to/repo
```
