from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import subprocess

@dataclass
class GitOps:
    repo_root: Path
    ring_file: Path

    def ensure_repo(self) -> None:
        if not (self.repo_root / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.repo_root, check=True)
            subprocess.run(["git", "add", "-A"], cwd=self.repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_root, check=True)
        self.ring_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.ring_file.exists():
            self.ring_file.write_text(json.dumps({"restore_points": []}, indent=2))

    def commit_approved(self, message: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.repo_root, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo_root, text=True).strip()
        self._push_restore_point(sha)
        return sha

    def _push_restore_point(self, sha: str) -> None:
        data = json.loads(self.ring_file.read_text())
        pts = data.get("restore_points", [])
        pts.append(sha)
        pts = pts[-3:]  # keep last 3
        data["restore_points"] = pts
        self.ring_file.write_text(json.dumps(data, indent=2))

    def list_restore_points(self) -> list[str]:
        data = json.loads(self.ring_file.read_text())
        return data.get("restore_points", [])

    def hard_reset_to(self, sha: str) -> None:
        subprocess.run(["git", "reset", "--hard", sha], cwd=self.repo_root, check=True)
