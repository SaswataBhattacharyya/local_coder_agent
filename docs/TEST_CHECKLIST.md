# Manual Test Checklist

## Server
- `POST /init` with valid repo_root returns ok
- `POST /init` with missing repo_root + allow_missing_repo=true returns ok
- `POST /query` returns plan or questions
- `POST /propose` returns diff, summary, risk
- `POST /revise_pending` updates diff
- `POST /reject` clears pending
- `POST /reset_context` clears pending and session
- `POST /mcp/allow` sets mcp_allowed=true
- `POST /mcp/revoke` sets mcp_allowed=false
- `GET /mcp/status` returns mcp_allowed + allowed_domains
- `POST /restore_remote` with empty disables
- `GET /models` returns model lists
- `POST /models/select` updates selection
- `POST /mcp/reload` reloads config
- `/session/start`, `/branch/create`, `/branch/switch` work
- `/agent_state/snapshot` + `/agent_state/restore` work
- `/repo_map` returns summary
- `/repo_map/rebuild` works
- `/task/submit` -> `/task/status` -> `/task/logs` works
- `GET /worker/status` returns running + stats
  - includes started_at + thread_id

## VSCode Extension
- Ping Server updates status
- Propose shows pending diff
- Approve applies patch locally (git apply) and clears pending
- Approve fallback (WorkspaceEdit) works when git unavailable
- Reject clears pending
- Reset Context clears pending and conversation
- MCP Allow / Revoke / Status work and show output
- Model dropdowns update selections
- Restore Remote buttons work

## VM/Runpod
- Public endpoint: serverUrl set to https://<public-url>
- SSH tunnel: serverUrl set to http://127.0.0.1:8010
