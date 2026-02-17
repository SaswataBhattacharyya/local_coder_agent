# Setup Guide (Interactive)

Start here:

1. Are you running the agent **locally** or in a **VM/Runpod**?

## Local Mode (recommended first)

1. In the repo root:

```bash
python3 bootstrap.py
```

2. In VSCode, set:

- `localCodeAgent.serverUrl` = `http://127.0.0.1:8010`

3. Open the **Local Code Agent** view in the right sidebar.

## VM / Runpod Mode

1. Run on the VM/Runpod:

```bash
python3 bootstrap.py
```

If using OpenAI/Gemini, place `keys.env` on the VM/Runpod (server side).

2. Choose connectivity:

### A) Public endpoint (fastest to set up)

- Expose port `8010` in your Runpod/VM control panel.
- Set VSCode setting:
  - `localCodeAgent.serverUrl` = `https://<public-runpod-url>`

### B) SSH tunnel (no public exposure)

On your local machine:

```bash
ssh -L 8010:127.0.0.1:8010 <user>@<vm-host>
```

Then set:
- `localCodeAgent.serverUrl` = `http://127.0.0.1:8010`

## VSCode Extension Install (Dev)

1. Open `vscode-extension/` in VSCode.
2. Install dependencies:

```bash
npm install
```

3. Build:

```bash
npm run build
```

4. Press `F5` to launch the Extension Development Host.

## VSCode Extension Install (VSIX)

```bash
npm install
npm run build
npm run package
```

Then install the `.vsix` from the VSCode Extensions UI.

## Model Selection + Keys

- Local models are listed in `configs/config.yaml` under `model_registry`.
- To use OpenAI or Gemini, create `keys.env` (see `keys.env.example`) and add:
  - `OPENAI_API_KEY=...`
  - `GEMINI_API_KEY=...`
- Do not commit `keys.env` to version control.
- The extension UI exposes dropdowns to choose Reasoner/Coder models.
- Selection persists in `configs/config.yaml` under `model_registry.selected`.

## Context Ingestion (Large Inputs)

If large context is sent from VSCode/MCP, the server will chunk and ingest it automatically.
Configure via `configs/config.yaml` under `context_ingest`.

## MCP Config (Cursor-style)

- Create `mcp.json` in the repo root to add MCP servers (see `mcp.json.example`).
- In VM/Runpod mode, `mcp.json` must exist on the VM/Runpod filesystem (server side).
- After editing, click **MCP Reload** in the extension UI (or call `/mcp/reload`).

## Helper Script

You can print setup steps based on environment:

```bash
python3 scripts/print_setup_steps.py
```
