from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any
import json
import time
import urllib.request


@dataclass
class RemoteOpenAIBackend:
    base_url: str
    model: str
    api_key: str = ""
    timeout: int = 60

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    self.base_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=self._headers(),
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                last_err = exc
                time.sleep(1 + attempt)
        raise RuntimeError(f"Remote backend request failed: {last_err}")

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        data = self._post(payload)
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            if "message" in choice and choice["message"]:
                return choice["message"].get("content", "")
            if "text" in choice:
                return choice.get("text", "")
        raise RuntimeError(f"Remote backend invalid response: {data}")

    def chat_with_images(self, messages: List[Dict[str, str]], images: List[Dict[str, str]], temperature: float = 0.2) -> str:
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
        payload = {
            "model": self.model,
            "messages": content_messages,
            "temperature": temperature,
        }
        data = self._post(payload)
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            if "message" in choice and choice["message"]:
                return choice["message"].get("content", "")
            if "text" in choice:
                return choice.get("text", "")
        raise RuntimeError(f"Remote backend invalid response: {data}")
