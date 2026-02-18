#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Start server in background
python3 "$ROOT/bootstrap.py" &
BOOT_PID=$!

# Wait a moment for server to start
sleep 3

# Start VSCode UI
"$ROOT/scripts/start_ui.sh"

# Keep bootstrap process in foreground
wait $BOOT_PID
