from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import yaml
from typing import Dict, Any

from mcp.stdio_client import MCPStdioClient


@dataclass
class MCPServerConfig:
    name: str
    command: list[str]
    env: dict[str, str]


class MCPRegistry:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self._clients: Dict[str, MCPStdioClient] = {}

    def load(self) -> Dict[str, MCPServerConfig]:
        data = _load_config(self.config_path)
        servers = {}
        for name, cfg in (data.get("servers") or {}).items():
            servers[name] = MCPServerConfig(
                name=name,
                command=list(cfg.get("command") or []),
                env=dict(cfg.get("env") or {}),
            )
        return servers

    def set_config_path(self, path: Path) -> None:
        self.config_path = path

    def reload(self) -> None:
        self.stop_all()

    def get_client(self, name: str) -> MCPStdioClient:
        servers = self.load()
        if name not in servers:
            raise KeyError(f"Unknown MCP server: {name}")
        if name in self._clients:
            return self._clients[name]
        cfg = servers[name]
        client = MCPStdioClient(command=cfg.command, env=cfg.env)
        client.start()
        self._clients[name] = client
        return client

    def stop_all(self) -> None:
        for client in self._clients.values():
            client.stop()
        self._clients = {}


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text())
    return yaml.safe_load(path.read_text()) or {}
