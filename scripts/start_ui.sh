#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="$ROOT/vscode-extension"
SERVER_URL="${LOCAL_CODE_AGENT_SERVER_URL:-http://127.0.0.1:8010}"

if ! command -v code >/dev/null 2>&1; then
  echo "ERROR: VSCode 'code' CLI not found. Install it: VSCode -> Command Palette -> 'Shell Command: Install code command in PATH'"
  exit 1
fi

# Write workspace settings
python3 - <<PY
import json
from pathlib import Path
root = Path("$ROOT")
settings_path = root / ".vscode" / "settings.json"
settings_path.parent.mkdir(parents=True, exist_ok=True)
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text())
    except Exception:
        data = {}
else:
    data = {}

data["localCodeAgent.serverUrl"] = "$SERVER_URL"
settings_path.write_text(json.dumps(data, indent=2))
PY

cd "$EXT_DIR"

if [ ! -d node_modules ]; then
  npm install
fi

npm run build

# Launch VSCode with the extension in dev mode
code --extensionDevelopmentPath "$EXT_DIR" --new-window "$ROOT"

echo "UI started. Server URL set to: $SERVER_URL"
