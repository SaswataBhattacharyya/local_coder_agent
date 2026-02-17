from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any
import sqlite3
from tree_sitter_languages import get_parser

SUPPORTED = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@dataclass
class DependencyGraph:
    repo_root: Path
    db_path: Path

    def init_db(self) -> None:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS deps(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            dep TEXT
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deps_file ON deps(file_path)")
        con.commit()
        con.close()

    def update_file(self, file_path: Path) -> None:
        lang = SUPPORTED.get(file_path.suffix)
        if not lang:
            return
        rel = str(file_path.relative_to(self.repo_root))
        src = file_path.read_bytes()
        parser = get_parser(lang)
        tree = parser.parse(src)
        deps = self._extract_deps(tree.root_node, src, lang)
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("DELETE FROM deps WHERE file_path = ?", (rel,))
        for dep in deps:
            cur.execute("INSERT INTO deps(file_path, dep) VALUES (?, ?)", (rel, dep))
        con.commit()
        con.close()

    def list_deps(self, limit: int = 1000) -> List[Dict[str, Any]]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        rows = cur.execute("SELECT file_path, dep FROM deps LIMIT ?", (limit,)).fetchall()
        con.close()
        return [{"file": r[0], "dep": r[1]} for r in rows]

    def _extract_deps(self, node, src: bytes, lang: str) -> List[str]:
        deps: List[str] = []
        stack = [node]
        while stack:
            n = stack.pop()
            if lang == "python" and n.type in {"import_statement", "import_from_statement"}:
                text = n.text.decode("utf-8", errors="ignore")
                deps.append(text)
            if lang in {"javascript", "typescript", "tsx"} and n.type in {"import_statement", "import_clause", "import"}:
                text = n.text.decode("utf-8", errors="ignore")
                if "from" in text or "require" in text:
                    deps.append(text)
            stack.extend(reversed(n.children))
        return deps
