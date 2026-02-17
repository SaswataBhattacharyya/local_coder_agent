from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import os

from rlm_wrap.store import RLMVarStore


def reset_context(repo_root: Path, minimal_meta: Dict[str, Any]) -> None:
    store = RLMVarStore(repo_root=repo_root)
    store.clear(keep_keys=[])
    if minimal_meta:
        store.set_many(minimal_meta)


def build_minimal_meta(repo_root: Path, head: str, model_cfg: Dict[str, Any], index_path: Path) -> Dict[str, Any]:
    meta = {
        "repo_root": str(repo_root),
        "head": head,
        "model_cfg": model_cfg,
    }
    try:
        meta["index_version"] = os.path.getmtime(index_path)
    except Exception:
        meta["index_version"] = None
    return meta
