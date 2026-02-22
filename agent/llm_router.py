from __future__ import annotations
from pathlib import Path
from typing import List, Dict

from agent.model_registry import resolve_model
from agent.keys import load_keys
from agent.providers import OpenAIChatProvider, GeminiChatProvider
from agent.llm_runtime import LlamaVLMRuntime, find_gguf_model
from rlm_wrap.runtime import RLMChatRuntime
from rlm_wrap.store import RLMVarStore
from agent.inference_backend import RemoteOpenAIBackend


def _role_backend(role: str, config) -> tuple[str, dict]:
    inf = getattr(config, "inference", None)
    if not inf:
        return "local", {}
    mode = (inf.mode or "local").lower()
    roles = getattr(inf, "roles", {}) or {}
    role_cfg = roles.get(role)
    backend = "local"
    if isinstance(role_cfg, dict):
        backend = (role_cfg.get("backend") or backend).lower()
    elif role_cfg is not None:
        backend = (getattr(role_cfg, "backend", backend) or backend).lower()
    if mode == "remote":
        backend = "remote"
    elif mode == "mixed":
        backend = backend or "local"
    else:
        backend = "local"
    return backend, role_cfg or {}


def backend_for_role(role: str, config) -> str:
    backend, _ = _role_backend(role, config)
    return backend


def _remote_backend_for_role(role: str, config) -> RemoteOpenAIBackend:
    backend, role_cfg = _role_backend(role, config)
    if backend != "remote":
        raise RuntimeError("remote backend requested but role not configured for remote")
    remote_url = ""
    model = ""
    api_key = ""
    if isinstance(role_cfg, dict):
        remote_url = role_cfg.get("remote_url", "")
        model = role_cfg.get("model", "")
        api_key = role_cfg.get("api_key", "")
    else:
        remote_url = getattr(role_cfg, "remote_url", "")
        model = getattr(role_cfg, "model", "")
        api_key = getattr(role_cfg, "api_key", "")
    if not remote_url:
        raise RuntimeError(f"remote_url not configured for role: {role}")
    if not model:
        # fallback: use default local model id if set
        model = getattr(getattr(config, role, None), "repo_id", "") or role
    return RemoteOpenAIBackend(base_url=remote_url, model=model, api_key=api_key)


def _has_local_model(role: str, config, repo_root: Path, config_path: Path | None = None) -> bool:
    try:
        model = resolve_model(role, config, repo_root, config_path=config_path)
        if model.provider != "local" or not model.model_dir:
            return False
        model_path = find_gguf_model(Path(config.paths.models_dir) / model.model_dir, model.filename_hint or "")
        return bool(model_path and Path(model_path).exists())
    except Exception:
        return False


def _local_chat(role: str, messages: List[Dict[str, str]], config, repo_root: Path, config_path: Path | None = None) -> str:
    model = resolve_model(role, config, repo_root, config_path=config_path)
    if model.provider == "local":
        if not model.model_dir:
            raise RuntimeError("local model_dir not configured")
        runtime = RLMChatRuntime(
            model_dir=Path(config.paths.models_dir) / model.model_dir,
            filename_hint=model.filename_hint or config.coder.filename_hint,
            n_ctx=model.context or config.coder.context,
            var_store=RLMVarStore(repo_root=repo_root),
            use_rlm=getattr(config.runtime, "use_rlm", False),
            rlm_backend=getattr(config.runtime, "rlm_backend", "openai"),
            rlm_backend_url=getattr(config.runtime, "rlm_backend_url", ""),
            rlm_backend_model=getattr(config.runtime, "rlm_backend_model", ""),
            rlm_backend_api_key=getattr(config.runtime, "rlm_backend_api_key", ""),
            rlm_max_depth=getattr(config.runtime, "rlm_max_depth", 1),
            rlm_max_iterations=getattr(config.runtime, "rlm_max_iterations", 30),
            repo_root=repo_root,
            rlm_environment=getattr(config.runtime, "rlm_environment", "local"),
            rlm_environment_kwargs=getattr(config.runtime, "rlm_environment_kwargs", None),
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


def chat(role: str, messages: List[Dict[str, str]], config, repo_root: Path, config_path: Path | None = None) -> str:
    backend, _ = _role_backend(role, config)
    if backend == "remote":
        remote = _remote_backend_for_role(role, config)
        try:
            return remote.chat(messages)
        except Exception as exc:
            if _has_local_model(role, config, repo_root, config_path=config_path):
                return _local_chat(role, messages, config, repo_root, config_path=config_path)
            raise RuntimeError(f"Remote inference failed for {role}: {exc}")
    return _local_chat(role, messages, config, repo_root, config_path=config_path)


def _local_chat_with_images(role: str, messages: List[Dict[str, str]], images: List[Dict[str, str]], config, repo_root: Path, config_path: Path | None = None) -> str:
    backend, _ = _role_backend(role, config)
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


def chat_with_images(role: str, messages: List[Dict[str, str]], images: List[Dict[str, str]], config, repo_root: Path, config_path: Path | None = None) -> str:
    backend, _ = _role_backend(role, config)
    if backend == "remote":
        remote = _remote_backend_for_role(role, config)
        try:
            return remote.chat_with_images(messages, images)
        except Exception as exc:
            if _has_local_model(role, config, repo_root, config_path=config_path):
                return _local_chat_with_images(role, messages, images, config, repo_root, config_path=config_path)
            raise RuntimeError(f"Remote inference failed for {role}: {exc}")
    return _local_chat_with_images(role, messages, images, config, repo_root, config_path=config_path)
