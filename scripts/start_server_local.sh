#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"

export LOCAL_CODE_AGENT_INFERENCE_MODE="${LOCAL_CODE_AGENT_INFERENCE_MODE:-local}"

python3 bootstrap.py
