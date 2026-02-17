from __future__ import annotations
import re
from pathlib import Path
from huggingface_hub import hf_hub_download, list_repo_files
from rich import print

def pick_gguf_file(repo_id: str, filename_hint: str) -> str:
    files = list_repo_files(repo_id)
    ggufs = [f for f in files if f.lower().endswith(".gguf")]
    if not ggufs:
        raise RuntimeError(f"No GGUF files found in repo {repo_id}")
    # Try hint match (e.g., Q4_K_M)
    hint = filename_hint.lower()
    for f in ggufs:
        if hint in f.lower():
            return f
    # Fallback: prefer Q4_K_M if present
    for f in ggufs:
        if "q4_k_m" in f.lower():
            return f
    # Fallback: smallest-ish (often Q4*)
    ggufs.sort(key=lambda x: (0 if "q4" in x.lower() else 1, len(x)))
    return ggufs[0]

def download_one(repo_id: str, filename_hint: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = pick_gguf_file(repo_id, filename_hint)
    print(f"[bold cyan]Downloading[/bold cyan] {repo_id} :: {filename}")
    local_path = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(out_dir), local_dir_use_symlinks=False)
    return Path(local_path)

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--hint", default="Q4_K_M")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    path = download_one(args.repo, args.hint, Path(args.out))
    print(f"[green]OK[/green] {path}")

if __name__ == "__main__":
    main()
