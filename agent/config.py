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

@dataclass
class RestoreCfg:
    remote_url: str = ""
    remote_name: str = "agent-restore"
    push_on_approve: bool = True

@dataclass
class AppConfig:
    paths: PathsCfg
    reasoner: ModelCfg
    coder: ModelCfg
    vlm: ModelCfg
    runtime: RuntimeCfg
    restore: RestoreCfg

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
    return AppConfig(paths=paths, reasoner=reasoner, coder=coder, vlm=vlm, runtime=runtime, restore=restore)
