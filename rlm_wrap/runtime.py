from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

from agent.llm_runtime import LlamaRuntime, find_gguf_model
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

    def _load_model_path(self) -> Path:
        return find_gguf_model(self.model_dir, self.filename_hint)

    def chat(self, messages: List[Dict[str, str]], vars: Dict[str, Any] | None = None) -> str:
        if self.var_store is not None and vars:
            self.var_store.set_many(vars)
        store_vars = self.var_store.load() if self.var_store is not None else {}
        try:
            import rlm  # type: ignore
            if hasattr(rlm, "chat"):
                return rlm.chat(messages=messages, vars=store_vars, session_id=self.session_id)
            if hasattr(rlm, "completion") and hasattr(rlm.completion, "chat"):
                return rlm.completion.chat(messages=messages, vars=store_vars, session_id=self.session_id)
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
