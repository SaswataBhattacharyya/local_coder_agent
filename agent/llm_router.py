from __future__ import annotations
from pathlib import Path
from typing import List, Dict

from agent.model_registry import resolve_model
from agent.keys import load_keys
from agent.providers import OpenAIChatProvider, GeminiChatProvider
from rlm_wrap.runtime import RLMChatRuntime
from rlm_wrap.store import RLMVarStore


def chat(role: str, messages: List[Dict[str, str]], config, repo_root: Path, config_path: Path | None = None) -> str:
    model = resolve_model(role, config, repo_root, config_path=config_path)
    if model.provider == "local":
        if not model.model_dir:
            raise RuntimeError("local model_dir not configured")
        runtime = RLMChatRuntime(
            model_dir=Path(config.paths.models_dir) / model.model_dir,
            filename_hint=model.filename_hint or config.coder.filename_hint,
            n_ctx=model.context or config.coder.context,
            var_store=RLMVarStore(repo_root=repo_root),
        )
        return runtime.chat(messages)
    if model.provider == "openai":
        keys = load_keys(repo_root)
        key = keys.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        provider = OpenAIChatProvider(api_key=key)
        return provider.chat(messages, model=model.model or "gpt-4o-mini")
    if model.provider == "gemini":
        keys = load_keys(repo_root)
        key = keys.get("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        provider = GeminiChatProvider(api_key=key)
        return provider.chat(messages, model=model.model or "gemini-2.5-flash")
    raise RuntimeError(f"unknown provider: {model.provider}")
