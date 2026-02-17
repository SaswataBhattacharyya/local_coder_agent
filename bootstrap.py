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

def set_restore_remote(url: str) -> None:
    cfg = ROOT / "configs" / "config.yaml"
    data = yaml.safe_load(cfg.read_text())
    data.setdefault("restore", {})
    data["restore"]["remote_url"] = url.strip()
    cfg.write_text(yaml.safe_dump(data, sort_keys=False))

def install_rlm() -> None:
    py = str(venv_python())
    # Install from github (latest)
    run([py, "-m", "pip", "install", "-U", "git+https://github.com/alexzhang13/rlm.git"], check=False)

def download_models(hint: str) -> None:
    py = str(venv_python())
    cfg = (ROOT/"configs"/"config.yaml").read_text()
    # quick parse without yaml for MVP:
    def get_line(prefix: str) -> str:
        for line in cfg.splitlines():
            if line.strip().startswith(prefix):
                return line.split(":",1)[1].strip()
        return ""
    models_dir = ROOT/"models"
    models_dir.mkdir(exist_ok=True)
    reasoner_repo = get_line("repo_id")
    # Actually: we download all three by calling script with explicit repos.
    repos = [
        ("reasoner", "lmstudio-community/DeepSeek-R1-Distill-Qwen-7B-GGUF"),
        ("coder", "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"),
    ]
    # VLM optional
    enable_vlm = "enabled: true" in cfg
    if enable_vlm:
        repos.append(("vlm", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF"))

    for name, repo in repos:
        out = models_dir / name
        run([py, "scripts/download_models.py", "--repo", repo, "--hint", hint, "--out", str(out)])

def start_server() -> None:
    py = str(venv_python())
    run([py, "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8010"])

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
    vram = ask_vram_gb()
    hint = choose_quant_hint(vram)
    print(f"[INFO] Using quant hint: {hint}")
    set_config_quant(hint)
    restore_url = input("Optional: enter git repo URL for restore backup (leave blank to disable revert backup): ").strip()
    if restore_url:
        set_restore_remote(restore_url)
    download_models(hint)
    print("[READY] Starting server at http://localhost:8010/docs")
    start_server()

if __name__ == "__main__":
    main()
