from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

@dataclass
class StagingArea:
    repo_root: Path
    staging_root: Path

    def ensure(self) -> None:
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        if self.staging_root.exists():
            shutil.rmtree(self.staging_root)
        self.ensure()

    def apply_unified_diff(self, diff_text: str) -> None:
        """Apply a unified diff to the staging copy of the repo.

        MVP approach: create a staging copy (rsync-like), then apply `git apply`.
        """
        self.ensure()
        # Create staging copy if empty
        if not any(self.staging_root.iterdir()):
            shutil.copytree(self.repo_root, self.staging_root, dirs_exist_ok=True)
        p = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=diff_text,
            text=True,
            cwd=self.staging_root,
            capture_output=True,
        )
        if p.returncode != 0:
            raise RuntimeError(f"git apply failed: {p.stderr.strip()}")
