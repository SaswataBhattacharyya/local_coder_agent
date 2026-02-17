from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List
import json
import sqlite3


@dataclass
class RepoMapBuilder:
    repo_root: Path
    index_db: Path
    dep_db: Path

    def build(self, out_dir: Path, limit_files: int = 200) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.index_db)
        cur = con.cursor()
        files = cur.execute("SELECT path, mtime FROM files LIMIT ?", (limit_files,)).fetchall()
        sym_counts = {r[0]: r[1] for r in cur.execute("SELECT file_path, COUNT(*) FROM symbols GROUP BY file_path").fetchall()}
        con.close()

        con2 = sqlite3.connect(self.dep_db)
        cur2 = con2.cursor()
        dep_counts = {r[0]: r[1] for r in cur2.execute("SELECT file_path, COUNT(*) FROM deps GROUP BY file_path").fetchall()}
        con2.close()

        stats = []
        for path, mtime in files:
            stats.append({
                "path": path,
                "mtime": mtime,
                "symbols": sym_counts.get(path, 0),
                "deps": dep_counts.get(path, 0),
            })
        stats.sort(key=lambda x: (x["deps"], x["symbols"]), reverse=True)

        repo_map = {
            "file_count": len(stats),
            "top_modules": stats[:10],
            "files": stats,
        }
        (out_dir / "repo_map.json").write_text(json.dumps(repo_map, indent=2))
        md_lines = ["# Repo Map", "", f"Files: {len(stats)}", "", "## Top Modules"]
        for s in stats[:10]:
            md_lines.append(f"- {s['path']} (deps: {s['deps']}, symbols: {s['symbols']})")
        (out_dir / "repo_map.md").write_text("\n".join(md_lines))
        return repo_map
