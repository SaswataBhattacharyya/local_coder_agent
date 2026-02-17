from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

def find_gguf_model(model_dir: Path, filename_hint: str) -> Path:
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")
    ggufs = sorted(model_dir.glob("*.gguf"))
    if not ggufs:
        raise FileNotFoundError(f"No GGUF files in: {model_dir}")
    hint = filename_hint.lower()
    for f in ggufs:
        if hint in f.name.lower():
            return f
    for f in ggufs:
        if "q4_k_m" in f.name.lower():
            return f
    return ggufs[0]
from llama_cpp import Llama

@dataclass
class LlamaRuntime:
    model_path: Path
    n_ctx: int = 8192
    n_gpu_layers: int = 0  # set based on VRAM
    temperature: float = 0.2

    def _make(self) -> Llama:
        return Llama(
            model_path=str(self.model_path),
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
            verbose=False,
        )

    def chat(self, messages: List[Dict[str, str]]) -> str:
        llm = self._make()
        out = llm.create_chat_completion(
            messages=messages,
            temperature=self.temperature,
        )
        return out["choices"][0]["message"]["content"]
