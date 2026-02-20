from __future__ import annotations
from pathlib import Path
from typing import List, Dict

from agent.model_registry import resolve_model
from agent.keys import load_keys
from agent.providers import OpenAIChatProvider, GeminiChatProvider
from agent.llm_runtime import LlamaVLMRuntime, find_gguf_model
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


def chat_with_images(role: str, messages: List[Dict[str, str]], images: List[Dict[str, str]], config, repo_root: Path, config_path: Path | None = None) -> str:
    model = resolve_model(role, config, repo_root, config_path=config_path)
    if model.provider == "local":
        if not model.model_dir:
            raise RuntimeError("local model_dir not configured")
        model_path = find_gguf_model(Path(config.paths.models_dir) / model.model_dir, model.filename_hint or config.vlm.filename_hint)
        vlm = LlamaVLMRuntime(
            model_path=model_path,
            n_ctx=model.context or config.vlm.context,
            n_gpu_layers=0,
            temperature=0.2,
        )
        # Convert messages to llama.cpp multimodal format: content list with image_url
        content_messages: List[Dict[str, Any]] = []
        last_user_idx = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        for i, m in enumerate(messages):
            if i == last_user_idx:
                parts: List[Dict[str, Any]] = [{"type": "text", "text": m["content"]}]
                for img in images:
                    url = img.get("data") or ""
                    if url:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                content_messages.append({"role": m["role"], "content": parts})
            else:
                content_messages.append({"role": m["role"], "content": m["content"]})
        return vlm.chat_with_images(content_messages)
    if model.provider == "openai":
        keys = load_keys(repo_root)
        key = keys.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        provider = OpenAIChatProvider(api_key=key)
        return provider.chat_with_images(messages, images, model=model.model or "gpt-4o-mini")
    if model.provider == "gemini":
        keys = load_keys(repo_root)
        key = keys.get("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        provider = GeminiChatProvider(api_key=key)
        return provider.chat_with_images(messages, images, model=model.model or "gemini-2.5-flash")
    raise RuntimeError(f"unknown provider: {model.provider}")
