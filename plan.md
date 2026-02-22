# fix12 Analysis (No Code Changes Yet)

## What I Understand You Want
You want a Cursor‑like split:
- **All file I/O + indexing stays local** (agent server runs on your laptop so it can see VS Code workspace directly).
- **Inference is optional and can be remote** (VM GPU via OpenAI‑compatible HTTP), without any repo mounting/rsync.
- **Minimal, incremental edits** that preserve the existing UI and logic.

## Key Gaps vs Current Repo
1) **bootstrap.py always downloads local GGUF models**. This breaks remote‑inference mode.
2) **No official “3 command workflow”** (local server, VM vLLM, tunnel) with scripts.
3) **Remote inference config is partially wired** (UI + config exists) but needs:
   - explicit mode selection in bootstrap
   - clear local‑server‑only scripts
4) **Token/RAM mitigation** needs stricter budgeting + map‑reduce fallback everywhere INFO is generated.
5) **Observability** missing: expose backend used, bytes read, top‑k, token estimates.
6) **Tests** missing for remote backend + “skip download in remote mode.”

## Proposed Changes (High‑Level)
### A) bootstrap.py (minimal changes)
- Add an **inference mode prompt** (local/remote/mixed) with env overrides.
- If mode is remote/mixed:
  - **skip GGUF downloads**
  - **start only FastAPI server**
- Persist mode into `configs/config.yaml`.

### B) Add 3 helper scripts (simple)
1. `scripts/start_server_local.sh` – start FastAPI only, no model downloads when remote/mixed.
2. `scripts/start_vm_vllm.sh` – run vLLM server(s) on VM (example ports 8000/8001/8002).
3. `scripts/start_vm_tunnel.sh` – open SSH tunnels (18080/18081/18082).

### C) Docs (minimal)
- Update README + docs/SETUP.md with **Cursor‑like flow**:
  1) run local server
  2) run VM vLLM
  3) run tunnel
  4) set Inference URLs in UI
- Explicitly say **no rsync/mount required**.

### D) Token/RAM mitigation
- Enforce **hard budget** for context assembly everywhere INFO is generated.
- Always exclude heavy dirs (node_modules, .git, dist, build, .next, .vite, venv, models, etc.) from context gathering.
- Use **retrieval top‑k** only, never dump full files.
- Add **map‑reduce fallback** if context size is still too large.

### E) Observability (lightweight)
Add trace spans / small UI facts line:
- files considered / read
- context bytes
- retrieval top‑k
- backend used per role
- token estimates

### F) Tests (small)
- RemoteOpenAIBackend: mocked happy path + timeout.
- bootstrap: remote mode skips downloads.

## What Must *Not* Change
- No UI redesign or workflow rewrite.
- No repo mounting requirement.
- No MCP changes.
- No planner rewrites.

## Execution Plan (Order)
1) **bootstrap.py**: add mode prompt, env overrides, skip downloads when remote/mixed.
2) **Scripts**: add start_local/server + vm_vllm + vm_tunnel scripts.
3) **Docs**: add 3‑command workflow and remote inference section.
4) **INFO pipeline safety**: strict budget + top‑k + map‑reduce fallback.
5) **Observability**: trace spans + small “facts line” in UI.
6) **Tests**: Remote backend + bootstrap skip‑download.

## Risks / Conflicts
- Must ensure remote inference mode **does not silently fall back** to local unless models exist; should emit clear error.
- Avoid touching existing UI layout beyond adding inference fields (already in place).
- Ensure any new exclusion logic doesn’t reduce summary quality too much.

---

If this plan matches your expectations, I will implement in the above order.
