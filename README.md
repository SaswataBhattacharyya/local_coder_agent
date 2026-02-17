# Local Code Agent (MVP scaffold)

This is a **bootstrappable** local coding agent scaffold designed for VM installs.

## Quick start

```bash
python3 bootstrap.py
```

The bootstrap script will:
- create a local `venv/`
- install python deps
- ask for GPU VRAM (or auto-detect)
- optionally ask for a restore git repo URL (for backup of restore points)
- download GGUF models (reasoner, coder, optional VLM)
- start the local FastAPI server

## Restore Remote (Optional)
- You can set or disable the restore remote after init:
  - `POST /restore_remote` with `{ "restore_remote_url": "https://..." }`
  - Empty or invalid URL disables restore backup.
  - Optional `push_on_approve: true|false` toggles automatic push on approval.

Then open:
- http://localhost:8010/docs

## Notes

- Shell commands are **gated**: the server will require explicit confirmation tokens.
- Edits are **staged** as pending patches until approved (git commit on approval).

## MCP (Optional)

An optional MCP bridge is available (including a free browser MCP via Playwright).
See `mcp/README.md` for setup and usage.
# local_coder_agent
