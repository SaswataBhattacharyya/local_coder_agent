#!/usr/bin/env python3
"""Bootstrapper for Local Code Agent (VM-friendly)

Inspired by the pattern in your example main.py:
- set up venv
- install deps
- (optional) install RLM
- download models
- start server

See your example for the style of stepwise setup. 
"""

from __future__ import annotations
import os, sys, subprocess, shutil
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent
VENV = ROOT / "venv"

def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"[RUN] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, check=check)

def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"

def ensure_venv() -> None:
    if VENV.exists():
        print("[OK] venv exists")
        return
    run([sys.executable, "-m", "venv", str(VENV)])
    print("[OK] venv created")

def pip_install() -> None:
    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip"], check=False)
    run([py, "-m", "pip", "install", "-r", "requirements.txt"])

def ask_vram_gb() -> int:
    # Try nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], text=True)
            v = int(out.strip().splitlines()[0])
            gb = max(1, round(v / 1024))
            print(f"[OK] Detected VRAM: ~{gb} GB")
            return gb
        except Exception:
            pass
    while True:
        s = input("Enter available GPU VRAM in GB (e.g., 8, 12, 24). If CPU-only, enter 0: ").strip()
        try:
            return int(s)
        except ValueError:
            print("Please enter an integer.")

def ask_inference_mode() -> str:
    env_mode = os.getenv("LOCAL_CODE_AGENT_INFERENCE_MODE", "").strip().lower()
    if env_mode in {"local", "remote", "mixed"}:
        print(f"[OK] Inference mode from env: {env_mode}")
        return env_mode
    while True:
        s = input("Inference mode? [local/remote/mixed] (default: local): ").strip().lower()
        if not s:
            return "local"
        if s in {"local", "remote", "mixed"}:
            return s
        print("Please enter local, remote, or mixed.")

def choose_quant_hint(vram_gb: int) -> str:
    # Simple heuristic:
    # - 0-8GB: Q4_K_M
    # - 9-12GB: Q5_K_M
    # - 13-24GB: Q6_K / Q8_0 depending preference
    if vram_gb <= 8:
        return "Q4_K_M"
    if vram_gb <= 12:
        return "Q5_K_M"
    if vram_gb <= 24:
        return "Q6_K"
    return "Q8_0"

def set_config_quant(hint: str) -> None:
    cfg = ROOT / "configs" / "config.yaml"
    data = yaml.safe_load(cfg.read_text())
    for key in ["reasoner", "coder", "vlm"]:
        if "models" in data and key in data["models"]:
            data["models"][key]["filename_hint"] = hint
    # Update model_registry local options
    registry = data.get("model_registry", {})
    for role in ["reasoner", "coder"]:
        options = (registry.get(role) or {}).get("options") or []
        for opt in options:
            if opt.get("provider") == "local":
                opt["filename_hint"] = hint
    cfg.write_text(yaml.safe_dump(data, sort_keys=False))

def set_inference_config(mode: str) -> None:
    cfg = ROOT / "configs" / "config.yaml"
    data = yaml.safe_load(cfg.read_text())
    inference = data.get("inference", {}) or {}
    inference["mode"] = mode
    roles = inference.get("roles", {}) or {}

    env = os.environ
    role_env = {
        "reasoner": ("LOCAL_CODE_AGENT_REASONER_URL", "LOCAL_CODE_AGENT_REASONER_MODEL"),
        "coder": ("LOCAL_CODE_AGENT_CODER_URL", "LOCAL_CODE_AGENT_CODER_MODEL"),
        "vlm": ("LOCAL_CODE_AGENT_VLM_URL", "LOCAL_CODE_AGENT_VLM_MODEL"),
    }
    for role, (url_key, model_key) in role_env.items():
        role_cfg = roles.get(role, {}) or {}
        remote_url = env.get(url_key, role_cfg.get("remote_url", ""))
        model = env.get(model_key, role_cfg.get("model", ""))
        backend = role_cfg.get("backend", "local")
        if mode == "remote":
            backend = "remote"
        elif mode == "mixed":
            backend = "remote" if remote_url else backend
        else:
            backend = "local"
        role_cfg.update(
            {
                "backend": backend,
                "remote_url": remote_url,
                "model": model,
                "api_key": role_cfg.get("api_key", ""),
            }
        )
        roles[role] = role_cfg
    inference["roles"] = roles
    data["inference"] = inference
    cfg.write_text(yaml.safe_dump(data, sort_keys=False))

def set_restore_remote(url: str) -> None:
    # Deprecated: git-based restore removed. Keep for backward compatibility.
    return None

def install_rlm() -> None:
    py = str(venv_python())
    # Install from github (latest)
    run([py, "-m", "pip", "install", "-U", "git+https://github.com/alexzhang13/rlm.git"], check=False)

def download_models(hint: str, roles_to_download: set[str]) -> None:
    py = str(venv_python())
    cfg = yaml.safe_load((ROOT/"configs"/"config.yaml").read_text())
    models_dir = ROOT/"models"
    models_dir.mkdir(exist_ok=True)

    repos: list[tuple[str, str, str]] = []
    # Base models
    models_cfg = cfg.get("models", {})
    for role in ["reasoner", "coder", "vlm"]:
        if role not in roles_to_download:
            continue
        m = models_cfg.get(role) or {}
        if role == "vlm" and not m.get("enabled", False):
            continue
        repo_id = m.get("repo_id")
        if repo_id:
            repos.append((role, repo_id, role))

    # Additional local options
    registry = (cfg.get("model_registry") or {})
    for role in ["reasoner", "coder", "vlm"]:
        if role not in roles_to_download:
            continue
        options = (registry.get(role) or {}).get("options") or []
        for opt in options:
            if opt.get("provider") != "local":
                continue
            repo_id = opt.get("repo_id")
            if not repo_id:
                continue
            model_dir = opt.get("model_dir") or opt.get("id") or role
            repos.append((role, repo_id, model_dir))

    seen = set()
    for role, repo, out_dir in repos:
        key = (repo, out_dir)
        if key in seen:
            continue
        seen.add(key)
        out = models_dir / out_dir
        run([py, "scripts/download_models.py", "--repo", repo, "--hint", hint, "--out", str(out)])

def start_server() -> None:
    py = str(venv_python())
    run([py, "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8010"])

def roles_to_download(mode: str, cfg: dict) -> set[str]:
    roles_to_download: set[str] = set()
    inference = cfg.get("inference", {}) or {}
    roles_cfg = inference.get("roles", {}) or {}
    for role in ["reasoner", "coder", "vlm"]:
        backend = (roles_cfg.get(role, {}) or {}).get("backend", "local")
        if mode == "local":
            roles_to_download.add(role)
        elif mode == "remote":
            continue
        else:
            if backend == "local":
                roles_to_download.add(role)
    return roles_to_download


def main():
    print("="*60)
    print("Local Code Agent - VM Bootstrap")
    print("="*60)
    ensure_venv()
    pip_install()
    try:
        install_rlm()
    except Exception:
        print("[WARN] RLM install failed; continuing")
    mode = ask_inference_mode()
    set_inference_config(mode)
    cfg = yaml.safe_load((ROOT/"configs"/"config.yaml").read_text())
    roles_to_download = roles_to_download(mode, cfg)
    if roles_to_download:
        vram = ask_vram_gb()
        hint = choose_quant_hint(vram)
        print(f"[INFO] Using quant hint: {hint}")
        set_config_quant(hint)
        print("Snapshot restore is local-only (no git). Use the UI History tab to create/restore snapshots.")
        download_models(hint, roles_to_download)
    else:
        print("[INFO] Remote inference enabled; skipping local model downloads.")
    print("[READY] Starting server at http://localhost:8010/docs")
    start_server()

if __name__ == "__main__":
    main()
