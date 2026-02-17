from __future__ import annotations
from pathlib import Path
from typing import Dict


def load_keys(repo_root: Path) -> Dict[str, str]:
    path = repo_root / "keys.env"
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip()
    return data
