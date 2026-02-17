from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Tuple
import json
import os
import re
import yaml


STATE_FILE = "mcp_state.json"


@dataclass
class MCPPolicy:
    allowed_domains: List[str]


def load_policy(config_path: Path) -> MCPPolicy:
    if not config_path.exists():
        return MCPPolicy(allowed_domains=[])
    data: Dict[str, Any] = {}
    try:
        if config_path.suffix.lower() == ".json":
            data = json.loads(config_path.read_text())
        else:
            data = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        data = {}
    policy = data.get("policy") or {}
    return MCPPolicy(allowed_domains=list(policy.get("allowed_domains") or []))


def load_state(repo_root: Path) -> Dict[str, Any]:
    path = repo_root / ".agent" / STATE_FILE
    if not path.exists():
        return {"mcp_allowed": False}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"mcp_allowed": False}


def save_state(repo_root: Path, state: Dict[str, Any]) -> None:
    path = repo_root / ".agent" / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def is_risky_tool(tool_name: str, arguments: Dict[str, Any], repo_root: Path, policy: MCPPolicy) -> Tuple[bool, str]:
    name = (tool_name or "").lower()
    # Tool names indicating risky actions
    risky_keywords = ["click", "type", "fill", "press", "submit", "form", "evaluate", "exec", "script", "download", "save", "write"]
    if any(k in name for k in risky_keywords):
        return True, "tool implies interaction or execution"
    # File path writes
    for key, val in (arguments or {}).items():
        if key.lower() in {"path", "file", "filepath", "filename"} and isinstance(val, str):
            p = Path(val)
            if p.is_absolute():
                try:
                    p.relative_to(repo_root)
                except ValueError:
                    return True, "file path outside repo"
            else:
                # relative path still potentially risky if not under repo; treat as risky if it escapes
                norm = (repo_root / p).resolve()
                try:
                    norm.relative_to(repo_root)
                except ValueError:
                    return True, "file path outside repo"
    # Downloads or navigation to non-whitelisted domains
    url = _extract_url(arguments or {})
    if url:
        if _looks_like_download(url):
            return True, "download detected"
        if policy.allowed_domains:
            domain = _domain_from_url(url)
            if domain and not _domain_allowed(domain, policy.allowed_domains):
                return True, "domain not in allowlist"
    return False, ""


def _extract_url(arguments: Dict[str, Any]) -> str | None:
    for key, val in arguments.items():
        if key.lower() in {"url", "href", "link"} and isinstance(val, str):
            return val
    return None


def _domain_from_url(url: str) -> str | None:
    m = re.match(r"^https?://([^/]+)", url.strip())
    if not m:
        return None
    return m.group(1).lower()


def _domain_allowed(domain: str, allowlist: List[str]) -> bool:
    for allowed in allowlist:
        allowed = allowed.lower()
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def _looks_like_download(url: str) -> bool:
    return any(url.lower().endswith(ext) for ext in [".zip", ".tar", ".tgz", ".gz", ".exe", ".dmg", ".pkg", ".whl", ".deb", ".rpm"])
