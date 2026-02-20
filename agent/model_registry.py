from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Optional
import json
import yaml

from agent.keys import load_keys
from agent.config import AppConfig


@dataclass
class ModelOption:
    id: str
    provider: str  # local | openai | gemini
    role: str  # reasoner | coder
    model: str | None = None
    model_dir: str | None = None
    filename_hint: str | None = None
    context: int | None = None
    repo_id: str | None = None


def load_registry(config: AppConfig, repo_root: Path) -> Dict[str, List[ModelOption]]:
    registry: Dict[str, List[ModelOption]] = {"reasoner": [], "coder": [], "vlm": []}
    cfg = getattr(config, "model_registry", None)
    if cfg:
        for role in ["reasoner", "coder", "vlm"]:
            options = (cfg.get(role) or {}).get("options") or []
            for opt in options:
                registry[role].append(ModelOption(**opt))
    # Add cloud providers if keys exist
    keys = load_keys(repo_root)
    if keys.get("OPENAI_API_KEY"):
        registry["reasoner"].append(ModelOption(id="openai-gpt-4o-mini", provider="openai", role="reasoner", model="gpt-4o-mini"))
        registry["coder"].append(ModelOption(id="openai-gpt-4o-mini", provider="openai", role="coder", model="gpt-4o-mini"))
        registry["vlm"].append(ModelOption(id="openai-gpt-4o-mini", provider="openai", role="vlm", model="gpt-4o-mini"))
    if keys.get("GEMINI_API_KEY"):
        registry["reasoner"].append(ModelOption(id="gemini-2.5-flash", provider="gemini", role="reasoner", model="gemini-2.5-flash"))
        registry["coder"].append(ModelOption(id="gemini-2.5-flash", provider="gemini", role="coder", model="gemini-2.5-flash"))
        registry["vlm"].append(ModelOption(id="gemini-2.5-flash", provider="gemini", role="vlm", model="gemini-2.5-flash"))
    # Add local VLM option if enabled in config
    try:
        if getattr(config, "vlm", None) and getattr(config.vlm, "enabled", False):
            registry["vlm"].append(ModelOption(
                id="local-vlm",
                provider="local",
                role="vlm",
                model_dir="vlm",
                filename_hint=config.vlm.filename_hint,
                context=config.vlm.context,
            ))
    except Exception:
        pass
    return registry


def get_defaults(config: AppConfig) -> Dict[str, str]:
    defaults = {"reasoner": "", "coder": "", "vlm": ""}
    cfg = getattr(config, "model_registry", None) or {}
    for role in ["reasoner", "coder", "vlm"]:
        defaults[role] = (cfg.get(role) or {}).get("default", "")
    return defaults


def get_selected_from_config(config_path: Path) -> Dict[str, str]:
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text()) or {}
    sel = (data.get("model_registry") or {}).get("selected") or {}
    return {"reasoner": sel.get("reasoner", ""), "coder": sel.get("coder", ""), "vlm": sel.get("vlm", "")}


def set_selected_in_config(config_path: Path, role: str, model_id: str) -> None:
    data = yaml.safe_load(config_path.read_text()) or {}
    data.setdefault("model_registry", {})
    data["model_registry"].setdefault("selected", {})
    data["model_registry"]["selected"][role] = model_id
    config_path.write_text(yaml.safe_dump(data, sort_keys=False))


def load_state(repo_root: Path) -> Dict[str, str]:
    path = repo_root / ".agent" / "model_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_state(repo_root: Path, state: Dict[str, str]) -> None:
    path = repo_root / ".agent" / "model_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def resolve_model(role: str, config: AppConfig, repo_root: Path, config_path: Path | None = None) -> ModelOption:
    registry = load_registry(config, repo_root)
    defaults = get_defaults(config)
    selected = "best"
    if config_path:
        cfg_sel = get_selected_from_config(config_path)
        selected = cfg_sel.get(role) or selected
    else:
        state = load_state(repo_root)
        selected = state.get(role) or selected
    if selected == "best":
        selected = defaults.get(role, "")
    for opt in registry.get(role, []):
        if opt.id == selected:
            return opt
    # fallback to first option
    if registry.get(role):
        return registry[role][0]
    raise RuntimeError(f"No models registered for role: {role}")


def list_models(role: str, config: AppConfig, repo_root: Path, config_path: Path | None = None) -> Dict[str, Any]:
    registry = load_registry(config, repo_root)
    defaults = get_defaults(config)
    selected = "best"
    if config_path:
        cfg_sel = get_selected_from_config(config_path)
        selected = cfg_sel.get(role) or selected
    else:
        state = load_state(repo_root)
        selected = state.get(role) or selected
    return {
        "role": role,
        "selected": selected,
        "default": defaults.get(role, ""),
        "options": [
            {"id": "best", "label": "Best (default)", "provider": "meta"}
        ] + [
            {
                "id": opt.id,
                "label": f"{opt.id} ({opt.provider})",
                "provider": opt.provider,
                "model": opt.model,
                "role": opt.role,
                "model_dir": opt.model_dir,
                "context": opt.context,
            }
            for opt in registry.get(role, [])
        ],
    }


def set_selected(role: str, model_id: str, repo_root: Path, config_path: Path | None = None) -> None:
    if config_path:
        set_selected_in_config(config_path, role, model_id)
        return
    state = load_state(repo_root)
    state[role] = model_id
    save_state(repo_root, state)
