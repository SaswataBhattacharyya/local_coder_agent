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
- download GGUF models (reasoner, coder, optional VLM)
- start the local FastAPI server

Then open:
- http://localhost:8010/docs

## Notes

- Shell commands are **gated**: the server will require explicit confirmation tokens.
- Edits are **staged** as pending patches until approved (git commit on approval).
# local_coder_agent
