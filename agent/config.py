from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass
class ModelCfg:
    repo_id: str
    filename_hint: str = "Q4_K_M"
    context: int = 8192
    enabled: bool = True

@dataclass
class PathsCfg:
    models_dir: Path
    index_dir: Path
    staging_dir: Path

@dataclass
class RuntimeCfg:
    host: str = "0.0.0.0"
    port: int = 8010
    allow_shell: bool = False
    use_rlm: bool = True
    rlm_backend: str = "openai"
    rlm_backend_url: str = ""
    rlm_backend_model: str = ""
    rlm_backend_api_key: str = ""
    rlm_max_depth: int = 1
    rlm_max_iterations: int = 30
    multi_step_edits: bool = True
    multi_step_max_files: int = 5
    multi_step_max_passes: int = 2

@dataclass
class RestoreCfg:
    remote_url: str = ""
    remote_name: str = "agent-restore"
    push_on_approve: bool = True

@dataclass
class ContextIngestCfg:
    enabled: bool = True
    max_chars: int = 12000
    chunk_size: int = 2000
    chunk_overlap: int = 200
    top_k: int = 6

@dataclass
class AppConfig:
    paths: PathsCfg
    reasoner: ModelCfg
    coder: ModelCfg
    vlm: ModelCfg
    runtime: RuntimeCfg
    restore: RestoreCfg
    model_registry: dict
    context_ingest: ContextIngestCfg

def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text())
    paths = PathsCfg(
        models_dir=Path(data["paths"]["models_dir"]),
        index_dir=Path(data["paths"]["index_dir"]),
        staging_dir=Path(data["paths"]["staging_dir"]),
    )
    m = data["models"]
    reasoner = ModelCfg(**m["reasoner"])
    coder = ModelCfg(**m["coder"])
    vlm = ModelCfg(**m["vlm"])
    runtime = RuntimeCfg(**data.get("runtime", {}))
    restore = RestoreCfg(**data.get("restore", {}))
    model_registry = data.get("model_registry", {})
    context_ingest = ContextIngestCfg(**data.get("context_ingest", {}))
    return AppConfig(paths=paths, reasoner=reasoner, coder=coder, vlm=vlm, runtime=runtime, restore=restore, model_registry=model_registry, context_ingest=context_ingest)
