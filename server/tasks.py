from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional
import json
import time
import threading


@dataclass
class Task:
    id: str
    type: str
    payload: Dict[str, Any]
    status: str = "queued"


class TaskQueue:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.tasks_dir = repo_root / ".agent" / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.queue_file = self.tasks_dir / "tasks.jsonl"
        self.lock = threading.Lock()

    def submit(self, task_type: str, payload: Dict[str, Any]) -> str:
        task_id = f"task_{int(time.time()*1000)}"
        task = {"id": task_id, "type": task_type, "payload": payload, "status": "queued", "ts": time.time()}
        with self.lock:
            with self.queue_file.open("a") as f:
                f.write(json.dumps(task) + "\n")
        self._write_meta(task_id, task)
        return task_id

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.queue_file.exists():
            return []
        lines = self.queue_file.read_text().splitlines()[-limit:]
        out = []
        for l in lines:
            if not l.strip():
                continue
            j = json.loads(l)
            meta = self._read_meta(j["id"])
            if meta:
                j.update(meta)
            out.append(j)
        return out

    def status(self, task_id: str) -> Dict[str, Any]:
        meta = self._read_meta(task_id)
        return meta or {"id": task_id, "status": "unknown"}

    def cancel(self, task_id: str) -> None:
        meta = self._read_meta(task_id) or {"id": task_id}
        meta["status"] = "cancelled"
        self._write_meta(task_id, meta)

    def _write_meta(self, task_id: str, meta: Dict[str, Any]) -> None:
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    def _read_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        path = self.tasks_dir / task_id / "meta.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def write_result(self, task_id: str, result: Dict[str, Any]) -> None:
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "result.json").write_text(json.dumps(result, indent=2))

    def append_log(self, task_id: str, message: str) -> None:
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        with (task_dir / "logs.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "msg": message}) + "\n")

    def read_logs(self, task_id: str, after: float | None = None) -> List[Dict[str, Any]]:
        path = self.tasks_dir / task_id / "logs.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
        logs = []
        for l in lines:
            if not l.strip():
                continue
            j = json.loads(l)
            if after is not None and j.get("ts", 0) <= after:
                continue
            logs.append(j)
        return logs


class TaskWorker(threading.Thread):
    def __init__(self, queue: TaskQueue, handler) -> None:
        super().__init__(daemon=True)
        self.queue = queue
        self.handler = handler
        self._stop = False
        self.last_tick = 0.0
        self.processed = 0
        self.current_task = ""
        self.last_error = ""
        self.started_at = time.time()

    def run(self) -> None:
        while not self._stop:
            self.last_tick = time.time()
            tasks = self.queue.list(limit=200)
            for t in tasks:
                if t.get("status") != "queued":
                    continue
                task_id = t["id"]
                self.current_task = task_id
                meta = self.queue.status(task_id)
                meta["status"] = "running"
                self.queue._write_meta(task_id, meta)
                try:
                    self.queue.append_log(task_id, f"running {t['type']}")
                    result = self.handler(t)
                    meta["status"] = "succeeded"
                    self.queue._write_meta(task_id, meta)
                    self.queue.write_result(task_id, result or {"ok": True})
                    self.processed += 1
                    self.current_task = ""
                    self.last_error = ""
                except Exception as exc:
                    meta["status"] = "failed"
                    meta["error"] = str(exc)
                    self.queue._write_meta(task_id, meta)
                    self.last_error = str(exc)
                    self.current_task = ""
            time.sleep(1)

    def stop(self) -> None:
        self._stop = True
