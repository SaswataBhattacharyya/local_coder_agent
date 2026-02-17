from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List
import json
import time


@dataclass
class AgentStateStore:
    repo_root: Path
    session_id: str

    def session_root(self) -> Path:
        return self.repo_root / ".agent" / "state" / "sessions" / self.session_id

    def active_branch_path(self) -> Path:
        return self.session_root() / "active_branch.txt"

    def branches_root(self) -> Path:
        return self.session_root() / "branches"

    def branch_root(self, branch: str) -> Path:
        return self.branches_root() / branch

    def ensure_session(self, branch: str = "main") -> None:
        self.branches_root().mkdir(parents=True, exist_ok=True)
        self.branch_root(branch).mkdir(parents=True, exist_ok=True)
        if not self.active_branch_path().exists():
            self.active_branch_path().write_text(branch)
        self._ensure_branch_files(branch)

    def _ensure_branch_files(self, branch: str) -> None:
        root = self.branch_root(branch)
        root.mkdir(parents=True, exist_ok=True)
        for name in ["state.json", "memory.md", "plan.md", "scratchpad.md"]:
            p = root / name
            if not p.exists():
                if name.endswith(".json"):
                    p.write_text("{}")
                else:
                    p.write_text("")
        (root / "tool_log.jsonl").touch(exist_ok=True)
        (root / "pending_patch.json").touch(exist_ok=True)
        (root / "repo_map").mkdir(parents=True, exist_ok=True)

    def get_active_branch(self) -> str:
        if not self.active_branch_path().exists():
            return "main"
        return self.active_branch_path().read_text().strip() or "main"

    def switch_branch(self, name: str) -> None:
        self._ensure_branch_files(name)
        self.active_branch_path().write_text(name)

    def list_branches(self) -> List[str]:
        if not self.branches_root().exists():
            return []
        return sorted([p.name for p in self.branches_root().iterdir() if p.is_dir()])

    def write_pending_patch(self, data: Dict[str, Any]) -> None:
        branch = self.get_active_branch()
        path = self.branch_root(branch) / "pending_patch.json"
        _atomic_write_json(path, data)

    def clear_pending_patch(self) -> None:
        self.write_pending_patch({})

    def read_pending_patch(self) -> Dict[str, Any]:
        branch = self.get_active_branch()
        path = self.branch_root(branch) / "pending_patch.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text() or "{}")
        except Exception:
            return {}

    def snapshot(self, head_sha: str, message: str = "") -> str:
        branch = self.get_active_branch()
        snap_id = f"snap_{int(time.time())}"
        snap_root = self.branch_root(branch) / "snapshots" / snap_id
        snap_root.mkdir(parents=True, exist_ok=True)
        # copy current branch files
        for name in ["state.json", "memory.md", "plan.md", "scratchpad.md", "pending_patch.json"]:
            src = self.branch_root(branch) / name
            if src.exists():
                (snap_root / name).write_text(src.read_text())
        meta = {"id": snap_id, "head": head_sha, "message": message, "ts": time.time()}
        _atomic_write_json(snap_root / "meta.json", meta)
        return snap_id

    def restore_snapshot(self, snapshot_id: str) -> None:
        branch = self.get_active_branch()
        snap_root = self.branch_root(branch) / "snapshots" / snapshot_id
        if not snap_root.exists():
            raise FileNotFoundError(f"snapshot not found: {snapshot_id}")
        for name in ["state.json", "memory.md", "plan.md", "scratchpad.md", "pending_patch.json"]:
            src = snap_root / name
            if src.exists():
                (self.branch_root(branch) / name).write_text(src.read_text())


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
