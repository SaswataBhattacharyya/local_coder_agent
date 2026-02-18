from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import subprocess

@dataclass
class GitOps:
    repo_root: Path
    ring_file: Path
    restore_remote_url: str | None = None
    restore_remote_name: str = "agent-restore"
    push_on_approve: bool = True

    def ensure_repo(self) -> None:
        if not (self.repo_root / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "agent@local"], cwd=self.repo_root, check=False)
            subprocess.run(["git", "config", "user.name", "Local Agent"], cwd=self.repo_root, check=False)
            subprocess.run(["git", "add", "-A"], cwd=self.repo_root, check=False)
            try:
                subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_root, check=True)
            except subprocess.CalledProcessError:
                # If nothing to commit, create an empty commit
                subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=self.repo_root, check=True)
        self.ring_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.ring_file.exists():
            self.ring_file.write_text(json.dumps({"restore_points": []}, indent=2))
        if self.restore_remote_url:
            self._ensure_restore_remote()

    def commit_approved(self, message: str) -> str:
        subprocess.run(["git", "add", "-A"], cwd=self.repo_root, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.repo_root, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo_root, text=True).strip()
        self._push_restore_point(sha)
        return sha

    def get_head(self) -> str:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo_root, text=True).strip()

    def status_dirty(self) -> bool:
        out = subprocess.check_output(["git", "status", "--porcelain"], cwd=self.repo_root, text=True).strip()
        return bool(out)

    def commit_message_from_diff(self, diff_text: str, fallback: str = "Approved change") -> str:
        files = []
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                files.append(line[6:].strip())
        if not files:
            return fallback
        if len(files) == 1:
            return f"Update {files[0]}"
        return f"Update {len(files)} files"

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

    def _ensure_restore_remote(self) -> None:
        name = self.restore_remote_name
        if not self.restore_remote_url:
            try:
                subprocess.run(["git", "remote", "remove", name], cwd=self.repo_root, check=True)
            except subprocess.CalledProcessError:
                pass
            return
        try:
            p = subprocess.run(["git", "remote", "get-url", name], cwd=self.repo_root, text=True, capture_output=True)
            if p.returncode != 0:
                raise subprocess.CalledProcessError(p.returncode, p.args, p.stdout, p.stderr)
            current = (p.stdout or "").strip()
            if current != self.restore_remote_url:
                subprocess.run(["git", "remote", "set-url", name, self.restore_remote_url], cwd=self.repo_root, check=True)
        except subprocess.CalledProcessError:
            subprocess.run(["git", "remote", "add", name, self.restore_remote_url], cwd=self.repo_root, check=True)

    def push_head(self) -> tuple[bool, str]:
        if not self.restore_remote_url:
            return False, "no restore remote configured"
        name = self.restore_remote_name
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo_root, text=True).strip()
        try:
            subprocess.run(["git", "push", name, f"HEAD:{branch}"], cwd=self.repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return True, "pushed"
        except subprocess.CalledProcessError as exc:
            return False, (exc.stderr or exc.stdout or "push failed")
