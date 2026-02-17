from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import subprocess
from typing import Iterable, List, Dict
from tree_sitter_languages import get_parser

SUPPORTED = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}

EXCLUDE_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__", ".agent", ".agent_index", ".agent_staging"}
@dataclass
class SymbolIndexer:
    repo_root: Path
    db_path: Path

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS files(
            path TEXT PRIMARY KEY,
            mtime REAL
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS symbols(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            kind TEXT,
            name TEXT,
            start_line INT,
            end_line INT
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path)")
        con.commit()
        con.close()

    def index_all(self) -> None:
        self.init_db()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("DELETE FROM symbols")
        cur.execute("DELETE FROM files")
        for p in self._iter_supported_files():
            self._index_file(cur, p)
        con.commit()
        con.close()

    def index_incremental(self) -> None:
        self.init_db()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        known = {row[0]: row[1] for row in cur.execute("SELECT path, mtime FROM files")}
        current = set()
        for p in self._iter_supported_files():
            rel = str(p.relative_to(self.repo_root))
            current.add(rel)
            mtime = p.stat().st_mtime
            if rel in known and float(known[rel]) == float(mtime):
                continue
            self._index_file(cur, p)
        removed = set(known.keys()) - current
        for rel in removed:
            cur.execute("DELETE FROM symbols WHERE file_path = ?", (rel,))
            cur.execute("DELETE FROM files WHERE path = ?", (rel,))
        con.commit()
        con.close()

    def _iter_supported_files(self) -> Iterable[Path]:
        for p in self.repo_root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            lang = SUPPORTED.get(p.suffix)
            if not lang:
                continue
            yield p

    def _index_file(self, cur: sqlite3.Cursor, path: Path) -> None:
        rel = str(path.relative_to(self.repo_root))
        lang = SUPPORTED.get(path.suffix)
        if not lang:
            return
        src = path.read_bytes()
        parser = get_parser(lang)
        tree = parser.parse(src)
        cur.execute("DELETE FROM symbols WHERE file_path = ?", (rel,))
        symbols = self._extract_symbols(tree.root_node, src, lang)
        for sym in symbols:
            cur.execute(
                "INSERT INTO symbols(file_path, kind, name, start_line, end_line) VALUES (?, ?, ?, ?, ?)",
                (rel, sym["kind"], sym["name"], sym["start_line"], sym["end_line"]),
            )
        cur.execute(
            "INSERT OR REPLACE INTO files(path, mtime) VALUES (?, ?)",
            (rel, path.stat().st_mtime),
        )

    def _extract_symbols(self, node, src: bytes, lang: str) -> List[Dict[str, object]]:
        symbols: List[Dict[str, object]] = []
        stack = [node]
        while stack:
            n = stack.pop()
            if lang == "python":
                if n.type == "function_definition":
                    name = n.child_by_field_name("name")
                    if name:
                        symbols.append(self._make_symbol("function", name, n))
                elif n.type == "class_definition":
                    name = n.child_by_field_name("name")
                    if name:
                        symbols.append(self._make_symbol("class", name, n))
            elif lang in {"javascript", "typescript", "tsx"}:
                if n.type == "function_declaration":
                    name = n.child_by_field_name("name")
                    if name:
                        symbols.append(self._make_symbol("function", name, n))
                elif n.type == "method_definition":
                    name = n.child_by_field_name("name")
                    if name:
                        symbols.append(self._make_symbol("method", name, n))
                elif n.type == "class_declaration":
                    name = n.child_by_field_name("name")
                    if name:
                        symbols.append(self._make_symbol("class", name, n))
                elif n.type == "variable_declarator":
                    init = n.child_by_field_name("value")
                    name = n.child_by_field_name("name")
                    if name and init and init.type in {"arrow_function", "function"}:
                        symbols.append(self._make_symbol("function", name, n))
            stack.extend(reversed(n.children))
        return symbols

    def _make_symbol(self, kind: str, name_node, parent_node) -> Dict[str, object]:
        name = name_node.text.decode("utf-8", errors="ignore")
        start_line = parent_node.start_point[0] + 1
        end_line = parent_node.end_point[0] + 1
        return {
            "kind": kind,
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
        }

    def rg_search(self, pattern: str, glob: str | None = None, max_results: int = 200) -> List[Dict[str, object]]:
        args = ["rg", "--line-number", "--column", "--no-heading", "--color", "never", pattern, str(self.repo_root)]
        if glob:
            args.extend(["-g", glob])
        p = subprocess.run(args, text=True, capture_output=True)
        if p.returncode not in (0, 1):
            raise RuntimeError(p.stderr.strip())
        results: List[Dict[str, object]] = []
        for line in p.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            file_path, line_no, col_no, text = parts
            try:
                results.append(
                    {
                        "file": str(Path(file_path).relative_to(self.repo_root)),
                        "line": int(line_no),
                        "column": int(col_no),
                        "text": text,
                    }
                )
            except Exception:
                continue
            if len(results) >= max_results:
                break
        return results

    def get_snippet(self, file_rel: str, line: int, window: int = 6, max_lines: int = 80) -> str:
        path = self.repo_root / file_rel
        if not path.exists():
            return ""
        lines = path.read_text(errors="ignore").splitlines()
        start = max(1, line - window)
        end = min(len(lines), line + window)
        if end - start + 1 > max_lines:
            end = min(len(lines), start + max_lines - 1)
        out = []
        for idx in range(start, end + 1):
            out.append(f"{idx:4d}: {lines[idx-1]}")
        return "\n".join(out)

    def get_file_head(self, file_rel: str, max_lines: int = 200) -> str:
        path = self.repo_root / file_rel
        if not path.exists():
            return ""
        lines = path.read_text(errors="ignore").splitlines()
        out = []
        for idx, line in enumerate(lines[:max_lines], start=1):
            out.append(f"{idx:4d}: {line}")
        return "\n".join(out)

    def search_symbols(self, name_substr: str, limit: int = 50) -> List[Dict[str, object]]:
        self.init_db()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        like = f"%{name_substr}%"
        rows = cur.execute(
            "SELECT file_path, kind, name, start_line, end_line FROM symbols WHERE name LIKE ? LIMIT ?",
            (like, limit),
        ).fetchall()
        con.close()
        return [
            {"file": r[0], "kind": r[1], "name": r[2], "start_line": r[3], "end_line": r[4]}
            for r in rows
        ]
