from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import time
import uuid


DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".agent",
    ".agent_stateless",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "models",
}

DEFAULT_EXCLUDE_FILES = {
    ".DS_Store",
}


@dataclass
class SnapshotMeta:
    snapshot_id: str
    created_at: float
    message: str
    file_count: int


class SnapshotCache:
    def __init__(
        self,
        repo_root: Path,
        max_snapshots: int = 3,
        cache_dir: Path | None = None,
        max_file_bytes: int = 10_000_000,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.max_snapshots = max_snapshots
        self.max_file_bytes = max_file_bytes
        self.cache_dir = cache_dir or (self.repo_root / ".agent" / "snapshots")
        self.index_path = self.cache_dir / "index.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index([])

    def _read_index(self) -> list[dict]:
        try:
            return json.loads(self.index_path.read_text())
        except Exception:
            return []

    def _write_index(self, data: list[dict]) -> None:
        self.index_path.write_text(json.dumps(data, indent=2))

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        for root, dirs, filenames in os.walk(self.repo_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(self.repo_root)
            # Prune excluded directories
            pruned = []
            for d in list(dirs):
                if d in DEFAULT_EXCLUDE_DIRS:
                    pruned.append(d)
                    continue
                if rel_root.parts and rel_root.parts[0] in DEFAULT_EXCLUDE_DIRS:
                    pruned.append(d)
                    continue
            for d in pruned:
                dirs.remove(d)

            for name in filenames:
                if name in DEFAULT_EXCLUDE_FILES:
                    continue
                p = root_path / name
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    if p.stat().st_size > self.max_file_bytes:
                        continue
                except Exception:
                    continue
                files.append(p)
        return files

    def snapshot(self, message: str = "") -> SnapshotMeta:
        snap_id = f"snap_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        snap_root = self.cache_dir / snap_id
        snap_root.mkdir(parents=True, exist_ok=True)
        files = self._iter_files()
        manifest: list[str] = []
        for p in files:
            rel = p.relative_to(self.repo_root)
            target = snap_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            manifest.append(str(rel))

        (snap_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
        meta = SnapshotMeta(
            snapshot_id=snap_id,
            created_at=time.time(),
            message=message,
            file_count=len(manifest),
        )
        index = self._read_index()
        index.append(meta.__dict__)
        index = index[-self.max_snapshots :]
        self._write_index(index)
        self._trim_old(index)
        self._write_head(snap_id)
        return meta

    def _trim_old(self, index: list[dict]) -> None:
        keep = {item["snapshot_id"] for item in index}
        for child in self.cache_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name not in keep:
                shutil.rmtree(child, ignore_errors=True)

    def list_snapshots(self) -> list[dict]:
        return self._read_index()

    def restore(self, snapshot_id: str) -> None:
        snap_root = self.cache_dir / snapshot_id
        manifest_path = snap_root / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"snapshot not found: {snapshot_id}")
        manifest = json.loads(manifest_path.read_text())
        wanted = set(manifest)

        # Remove files not present in snapshot
        for p in self._iter_files():
            rel = str(p.relative_to(self.repo_root))
            if rel not in wanted:
                try:
                    p.unlink()
                except Exception:
                    pass

        # Restore snapshot files
        for rel in manifest:
            src = snap_root / rel
            dst = self.repo_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copy2(src, dst)
        self._write_head(snapshot_id)

    def _write_head(self, snapshot_id: str) -> None:
        head_path = self.cache_dir / "head.json"
        head_path.write_text(json.dumps({"head": snapshot_id}, indent=2))

    def get_head(self) -> str:
        head_path = self.cache_dir / "head.json"
        if not head_path.exists():
            return "working"
        try:
            data = json.loads(head_path.read_text())
            return data.get("head", "working")
        except Exception:
            return "working"
