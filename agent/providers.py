from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict
import json
import urllib.request
import time


@dataclass
class OpenAIChatProvider:
    api_key: str
    base_url: str = "https://api.openai.com/v1/chat/completions"

    def chat(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    self.base_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                last_err = exc
                time.sleep(1 + attempt)
        raise RuntimeError(f"OpenAI request failed: {last_err}")


@dataclass
class GeminiChatProvider:
    api_key: str
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    def chat(self, messages: List[Dict[str, str]], model: str, temperature: float = 0.2) -> str:
        contents = _messages_to_gemini_contents(messages)
        url = f"{self.base_url}/models/{model}:generateContent"
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "x-goog-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as exc:
                last_err = exc
                time.sleep(1 + attempt)
        raise RuntimeError(f"Gemini request failed: {last_err}")


def _messages_to_gemini_contents(messages: List[Dict[str, str]]) -> List[Dict[str, object]]:
    contents: List[Dict[str, object]] = []
    system_parts = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
    system_prefix = "".join([f"[System]\n{p}\n" for p in system_parts])
    for m in messages:
        if m["role"] == "system":
            continue
        role = "user" if m["role"] == "user" else "model"
        text = m["content"]
        if role == "user" and system_prefix:
            text = system_prefix + "\n" + text
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents
