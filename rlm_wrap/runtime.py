from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

from agent.llm_runtime import LlamaRuntime, find_gguf_model
from agent.keys import load_keys
from rlm_wrap.store import RLMVarStore


@dataclass
class RLMChatRuntime:
    model_dir: Path
    filename_hint: str
    n_ctx: int = 8192
    n_gpu_layers: int = 0
    temperature: float = 0.2
    session_id: str = "default"
    var_store: RLMVarStore | None = None
    use_rlm: bool = False
    rlm_backend: str = "openai"
    rlm_backend_url: str = ""
    rlm_backend_model: str = ""
    rlm_backend_api_key: str = ""
    rlm_max_depth: int = 1
    rlm_max_iterations: int = 30
    repo_root: Path | None = None
    rlm_environment: str = "local"
    rlm_environment_kwargs: Dict[str, Any] | None = None

    def _load_model_path(self) -> Path:
        return find_gguf_model(self.model_dir, self.filename_hint)

    def _resolve_api_key(self) -> str:
        if self.rlm_backend_api_key:
            return self.rlm_backend_api_key
        if not self.repo_root:
            return ""
        keys = load_keys(self.repo_root)
        backend = (self.rlm_backend or "").lower()
        if backend == "openai":
            return keys.get("OPENAI_API_KEY", "")
        if backend == "anthropic":
            return keys.get("ANTHROPIC_API_KEY", "")
        if backend == "openrouter":
            return keys.get("OPENROUTER_API_KEY", "")
        if backend == "portkey":
            return keys.get("PORTKEY_API_KEY", "")
        if backend == "litellm":
            return keys.get("LITELLM_API_KEY", "")
        return ""

    def _build_backend_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if self.rlm_backend_model:
            kwargs["model_name"] = self.rlm_backend_model
        if self.rlm_backend_url:
            kwargs["base_url"] = self.rlm_backend_url
        api_key = self._resolve_api_key()
        if api_key:
            kwargs["api_key"] = api_key
        return kwargs

    def chat(self, messages: List[Dict[str, str]], vars: Dict[str, Any] | None = None) -> str:
        if self.var_store is not None and vars:
            self.var_store.set_many(vars)
        store_vars = self.var_store.load() if self.var_store is not None else {}
        try:
            if self.use_rlm and self.rlm_backend:
                from rlm import RLM  # type: ignore
                rlm_kwargs: Dict[str, Any] = {
                    "backend": self.rlm_backend,
                    "max_depth": self.rlm_max_depth,
                    "max_iterations": self.rlm_max_iterations,
                }
                if self.rlm_environment:
                    rlm_kwargs["environment"] = self.rlm_environment
                if self.rlm_environment_kwargs:
                    rlm_kwargs["environment_kwargs"] = self.rlm_environment_kwargs
                backend_kwargs = self._build_backend_kwargs()
                if backend_kwargs:
                    rlm_kwargs["backend_kwargs"] = backend_kwargs
                rlm = RLM(**rlm_kwargs)
                prompt = {"messages": messages, "memory": store_vars}
                root_prompt = (
                    "You are a coding assistant. You can access long-term memory in a JSON object named `context`. "
                    "The `context` contains keys `messages` and `memory`. Use `memory` when details are needed. "
                    "Respond with the final answer only."
                )
                result = rlm.completion(prompt=prompt, root_prompt=root_prompt)
                return getattr(result, "response", None) or str(result)
        except Exception:
            pass
        model_path = self._load_model_path()
        llm = LlamaRuntime(
            model_path=model_path,
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
            temperature=self.temperature,
        )
        return llm.chat(messages)
