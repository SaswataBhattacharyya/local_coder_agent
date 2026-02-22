#!/usr/bin/env bash
set -euo pipefail

# Run on VM. Starts OpenAI-compatible vLLM servers.
# Edit model names and ports as needed.

REASONER_MODEL="${LOCAL_CODE_AGENT_REASONER_MODEL:-<reasoner-model-name>}"
CODER_MODEL="${LOCAL_CODE_AGENT_CODER_MODEL:-<coder-model-name>}"
VLM_MODEL="${LOCAL_CODE_AGENT_VLM_MODEL:-<vlm-model-name>}"

REASONER_PORT="${LOCAL_CODE_AGENT_REASONER_PORT:-8000}"
CODER_PORT="${LOCAL_CODE_AGENT_CODER_PORT:-8001}"
VLM_PORT="${LOCAL_CODE_AGENT_VLM_PORT:-8002}"

echo "[INFO] Starting vLLM servers..."

# Reasoner
python -m vllm.entrypoints.openai.api_server \
  --model "$REASONER_MODEL" \
  --host 0.0.0.0 --port "$REASONER_PORT" &

# Coder
python -m vllm.entrypoints.openai.api_server \
  --model "$CODER_MODEL" \
  --host 0.0.0.0 --port "$CODER_PORT" &

# VLM (if supported)
python -m vllm.entrypoints.openai.api_server \
  --model "$VLM_MODEL" \
  --host 0.0.0.0 --port "$VLM_PORT" &

echo "[READY] Reasoner: http://127.0.0.1:${REASONER_PORT}/v1/chat/completions"
echo "[READY] Coder:    http://127.0.0.1:${CODER_PORT}/v1/chat/completions"
echo "[READY] VLM:      http://127.0.0.1:${VLM_PORT}/v1/chat/completions"

wait
