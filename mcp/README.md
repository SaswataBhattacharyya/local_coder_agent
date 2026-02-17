# MCP Integration (Optional)

This module provides an optional MCP (Model Context Protocol) bridge for external tools.
It uses a stdio JSON-RPC client and can launch MCP servers on demand with explicit `YES` confirmation.

## Included Browser MCP (Free)
Configured by default:
- `playwright` via `@modelcontextprotocol/server-playwright`

Config file: `configs/mcp.yaml`
Override (Cursor-style): create `mcp.json` in repo root. Example: `mcp.json.example`.

## Endpoints
- `POST /mcp/list_tools` with `{ "server": "playwright", "confirm": "YES" }`
- `POST /mcp/call` with `{ "server": "playwright", "tool": "<tool_name>", "arguments": { ... }, "confirm": "YES" }`
- `POST /mcp/allow` with `{ "confirm": "YES" }` (one-time allow for auto MCP use)
- `POST /mcp/revoke` with `{ "confirm": "YES" }`
- `POST /mcp/reload` reloads config (useful after editing `mcp.json`)

## Installing Playwright MCP
Requires Node + npm:
```bash
npm --version
npx -y @modelcontextprotocol/server-playwright --help
```

## Policy + Persistent Allow
- MCP allow is stored per repo in `.agent/mcp_state.json`.
- Risky actions (form submissions, downloads, script execution, file writes outside repo, non-allowlisted domains) require explicit `YES` on each call.
- You can add allowlisted domains in `configs/mcp.yaml` under `policy.allowed_domains`.

## Adding Other MCP Servers Later
1. Add a new entry to `configs/mcp.yaml`:
```
servers:
  your_server:
    command:
      - <your_command>
      - <arg1>
    env:
      YOUR_ENV: "value"
```
2. Call the endpoints with `"server": "your_server"` and `"confirm": "YES"`.
3. If the server needs secrets, set them in `env` or your shell environment.
