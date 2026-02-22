#!/usr/bin/env bash
set -euo pipefail

VM_HOST="${LOCAL_CODE_AGENT_VM_HOST:-}"
VM_USER="${LOCAL_CODE_AGENT_VM_USER:-root}"
VM_PORT="${LOCAL_CODE_AGENT_VM_SSH_PORT:-22}"
VM_KEY="${LOCAL_CODE_AGENT_VM_SSH_KEY:-$HOME/.ssh/id_ed25519}"

if [ -z "$VM_HOST" ]; then
  echo "ERROR: LOCAL_CODE_AGENT_VM_HOST is required"
  exit 1
fi

echo "[INFO] Opening SSH tunnels..."
echo "Reasoner -> http://127.0.0.1:18080/v1/chat/completions"
echo "Coder    -> http://127.0.0.1:18081/v1/chat/completions"
echo "VLM      -> http://127.0.0.1:18082/v1/chat/completions"

ssh -L 18080:127.0.0.1:8000 \
    -L 18081:127.0.0.1:8001 \
    -L 18082:127.0.0.1:8002 \
    "${VM_USER}@${VM_HOST}" -p "${VM_PORT}" -i "${VM_KEY}"
