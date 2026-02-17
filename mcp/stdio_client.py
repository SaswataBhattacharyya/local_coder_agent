from __future__ import annotations
from dataclasses import dataclass
import json
import subprocess
from typing import Dict, Any, List, Optional


def _encode_message(payload: Dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    return header + body


def _read_message(stream) -> Dict[str, Any]:
    headers = {}
    line = stream.readline()
    if not line:
        raise RuntimeError("MCP server closed the stream")
    while line and line.strip():
        k, v = line.decode("utf-8").split(":", 1)
        headers[k.lower()] = v.strip()
        line = stream.readline()
    length = int(headers.get("content-length", "0"))
    body = stream.read(length)
    if not body:
        raise RuntimeError("Empty MCP response body")
    return json.loads(body.decode("utf-8"))


@dataclass
class MCPStdioClient:
    command: List[str]
    env: Optional[Dict[str, str]] = None
    process: subprocess.Popen | None = None
    _id: int = 0

    def start(self) -> None:
        if self.process is not None:
            return
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=self.env,
        )
        self._initialize()

    def _initialize(self) -> None:
        self._send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "local-code-agent", "version": "0.1"},
                "capabilities": {},
            },
        })
        _read_message(self.process.stdout)  # initialize response
        self._send({
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        })

    def list_tools(self) -> Dict[str, Any]:
        self._send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        })
        return _read_message(self.process.stdout)

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self._send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        return _read_message(self.process.stdout)

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            self.process.terminate()
        finally:
            self.process = None

    def _send(self, payload: Dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("MCP process not started")
        msg = _encode_message(payload)
        self.process.stdin.write(msg)
        self.process.stdin.flush()

    def _next_id(self) -> int:
        self._id += 1
        return self._id
