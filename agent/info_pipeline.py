from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import List, Tuple


_README_RE = re.compile(r"^README(?:\\.[A-Za-z0-9]+)?$", re.IGNORECASE)
_PORT_RE = re.compile(r"(?:localhost|127\\.0\\.0\\.1|0\\.0\\.0\\.0)[: ](\\d{2,5})")
_DOCKER_PORT_RE = re.compile(r"\\b(\\d{2,5}):(\\d{2,5})\\b")
_SECTION_RE = re.compile(r"^#{1,6}\\s+(.*)$")
_FENCE_RE = re.compile(r"^```\\s*(\\w+)?\\s*$")


@dataclass
class InfoAnswer:
    summary: str
    start_commands: List[str]
    notes: List[str]
    ports: List[str]

    def render(self) -> str:
        parts: List[str] = []
        parts.append("Project Summary:")
        parts.append(f"- {self.summary}")
        parts.append("")
        parts.append("How to Start:")
        if self.start_commands:
            for cmd in self.start_commands:
                parts.append(f"- {cmd}")
        else:
            parts.append("- No obvious start command detected. Check README and scripts.")
        parts.append("")
        parts.append("Prerequisites / Notes:")
        if self.notes:
            for note in self.notes:
                parts.append(f"- {note}")
        else:
            parts.append("- None detected.")
        parts.append("")
        parts.append("Ports:")
        if self.ports:
            for port in self.ports:
                parts.append(f"- {port}")
        else:
            parts.append("- None detected.")
        return "\n".join(parts).strip()


def generate_info_answer(repo_root: Path) -> InfoAnswer:
    readme_path, readme_text = _load_readme(repo_root)
    summary = _summarize_readme(readme_text) if readme_text else _fallback_summary(repo_root)
    start_cmds = _detect_start_commands(repo_root, readme_text)
    notes = _detect_notes(repo_root, readme_text)
    ports = _detect_ports(readme_text, repo_root)
    if readme_path:
        notes.insert(0, f"Summary based on {readme_path.name}.")
    return InfoAnswer(summary=summary, start_commands=start_cmds, notes=notes, ports=ports)


def _load_readme(repo_root: Path) -> Tuple[Path | None, str | None]:
    for p in sorted(repo_root.iterdir()):
        if p.is_file() and _README_RE.match(p.name):
            try:
                return p, p.read_text(errors="ignore")
            except Exception:
                return p, None
    return None, None


def _summarize_readme(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        return "No README content found."
    # Prefer first heading + first paragraph
    heading = ""
    if lines[0].startswith("#"):
        heading = lines[0].lstrip("#").strip()
        lines = lines[1:]
    para: List[str] = []
    for ln in lines:
        if not ln:
            if para:
                break
            continue
        if ln.startswith("#"):
            break
        para.append(ln)
        if len(para) >= 4:
            break
    summary = " ".join(para).strip()
    if heading and summary:
        return f"{heading}: {summary}"
    if heading:
        return heading
    if summary:
        return summary
    return "README exists but no summary paragraph was detected."


def _fallback_summary(repo_root: Path) -> str:
    items = [p.name for p in sorted(repo_root.iterdir()) if not p.name.startswith(".")]
    if not items:
        return "Empty repository."
    return "Repository with top-level items: " + ", ".join(items[:10])


def _detect_start_commands(repo_root: Path, readme_text: str | None) -> List[str]:
    cmds: List[str] = []
    readme_cmds = _extract_readme_commands(readme_text)
    cmds.extend(readme_cmds)
    pkg = repo_root / "package.json"
    if pkg.exists():
        cmds.extend(_npm_start_cmds(pkg, repo_root))
    cmds.extend(_make_start_cmds(repo_root / "Makefile"))
    cmds.extend(_docker_start_cmds(repo_root / "docker-compose.yml", repo_root / "docker-compose.yaml"))
    cmds.extend(_python_start_cmds(repo_root))
    cmds.extend(_framework_start_cmds(repo_root))
    # De-dup while preserving order
    seen = set()
    out = []
    for c in cmds:
        if c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= 3:
            break
    return out


def _npm_start_cmds(pkg_path: Path, repo_root: Path) -> List[str]:
    try:
        data = json.loads(pkg_path.read_text())
    except Exception:
        return []
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return []
    tool = _detect_node_tool(repo_root)
    priority = ["dev", "start", "serve", "preview"]
    cmds = []
    for key in priority:
        if key in scripts:
            cmds.append(f"{tool} run {key}" if tool != "npm" else f"npm run {key}")
    return cmds


def _detect_node_tool(repo_root: Path) -> str:
    if (repo_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo_root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _make_start_cmds(makefile: Path) -> List[str]:
    if not makefile.exists():
        return []
    try:
        text = makefile.read_text(errors="ignore")
    except Exception:
        return []
    targets = []
    for line in text.splitlines():
        if ":" in line and not line.startswith("\t"):
            tgt = line.split(":", 1)[0].strip()
            if tgt in {"run", "start", "dev", "serve"}:
                targets.append(f"make {tgt}")
    return targets


def _docker_start_cmds(*paths: Path) -> List[str]:
    for p in paths:
        if p.exists():
            return ["docker compose up"]
    return []


def _python_start_cmds(repo_root: Path) -> List[str]:
    # Heuristic: common entrypoints
    if (repo_root / "manage.py").exists():
        return ["python manage.py runserver"]
    if (repo_root / "app.py").exists():
        return ["python app.py"]
    if (repo_root / "main.py").exists():
        return ["python main.py"]
    return []


def _framework_start_cmds(repo_root: Path) -> List[str]:
    cmds: List[str] = []
    # JS frameworks
    if list(repo_root.glob("next.config.*")):
        tool = _detect_node_tool(repo_root)
        cmds.append(f"{tool} run dev" if tool != "npm" else "npm run dev")
    if list(repo_root.glob("vite.config.*")):
        tool = _detect_node_tool(repo_root)
        cmds.append(f"{tool} run dev" if tool != "npm" else "npm run dev")
    # Python frameworks
    if (repo_root / "pyproject.toml").exists() or (repo_root / "requirements.txt").exists():
        if (repo_root / "app").exists() and (repo_root / "app" / "__init__.py").exists():
            cmds.append("uvicorn app:app --reload")
        if (repo_root / "main.py").exists():
            cmds.append("python main.py")
    return cmds


def _detect_notes(repo_root: Path, readme_text: str | None) -> List[str]:
    notes: List[str] = []
    if (repo_root / "requirements.txt").exists() or (repo_root / "pyproject.toml").exists():
        notes.append("Python dependencies detected (requirements.txt/pyproject.toml).")
    if (repo_root / "package.json").exists():
        notes.append("Node.js dependencies detected (package.json).")
    if (repo_root / ".env.example").exists():
        notes.append("Environment variables may be required (.env.example found).")
    if readme_text:
        for ln in readme_text.splitlines():
            if "Prerequisite" in ln or "Requirements" in ln:
                notes.append("README mentions prerequisites/requirements.")
                break
    return notes


def _extract_readme_commands(readme_text: str | None) -> List[str]:
    if not readme_text:
        return []
    lines = readme_text.splitlines()
    section = ""
    in_fence = False
    fence_lang = ""
    cmds: List[str] = []
    for ln in lines:
        m = _SECTION_RE.match(ln.strip())
        if m:
            section = m.group(1).lower()
            continue
        fence = _FENCE_RE.match(ln.strip())
        if fence:
            if in_fence:
                in_fence = False
                fence_lang = ""
            else:
                in_fence = True
                fence_lang = (fence.group(1) or "").lower()
            continue
        if not in_fence:
            continue
        if section and not any(k in section for k in ["quick start", "getting started", "usage", "run"]):
            continue
        if fence_lang and fence_lang not in {"bash", "sh", "shell", "console", "zsh"}:
            continue
        line = ln.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("$"):
            line = line[1:].strip()
        cmds.append(line)
    return cmds


def _detect_ports(readme_text: str | None, repo_root: Path) -> List[str]:
    ports: List[str] = []
    if readme_text:
        for match in _PORT_RE.finditer(readme_text):
            ports.append(f"localhost:{match.group(1)}")
    for compose in (repo_root / "docker-compose.yml", repo_root / "docker-compose.yaml"):
        if compose.exists():
            try:
                text = compose.read_text(errors="ignore")
            except Exception:
                continue
            for match in _DOCKER_PORT_RE.finditer(text):
                host, container = match.group(1), match.group(2)
                ports.append(f"{host}->{container}")
    # De-dup
    seen = set()
    out = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
