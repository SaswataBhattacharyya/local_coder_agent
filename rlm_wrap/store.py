from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Dict, Any, Iterable


@dataclass
class RLMVarStore:
    repo_root: Path
    filename: str = "rlm_vars.json"
    _cache: Dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.repo_root / ".agent" / self.filename

    def load(self) -> Dict[str, Any]:
        if self._cache:
            return dict(self._cache)
        if not self.path.exists():
            self._cache = {}
            return {}
        try:
            self._cache = json.loads(self.path.read_text())
        except Exception:
            self._cache = {}
        return dict(self._cache)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._cache, indent=2))

    def set(self, key: str, value: Any) -> None:
        self._cache = self.load()
        self._cache[key] = value
        self.save()

    def set_many(self, values: Dict[str, Any]) -> None:
        self._cache = self.load()
        self._cache.update(values)
        self.save()

    def clear(self, keep_keys: Iterable[str] | None = None) -> None:
        keep = set(keep_keys or [])
        current = self.load()
        self._cache = {k: v for k, v in current.items() if k in keep}
        self.save()
