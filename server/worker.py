from __future__ import annotations
from pathlib import Path
import time
import json
from server.tasks import TaskQueue


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()
    repo_root = Path(args.repo).resolve()
    q = TaskQueue(repo_root)
    while True:
        tasks = q.list(limit=100)
        for t in tasks:
            if t.get("status") != "queued":
                continue
            task_id = t["id"]
            meta = q.status(task_id)
            meta["status"] = "running"
            q._write_meta(task_id, meta)
            q.append_log(task_id, f"running {t['type']}")
            # placeholder: no-op
            time.sleep(0.1)
            meta["status"] = "succeeded"
            q._write_meta(task_id, meta)
            q.write_result(task_id, {"ok": True})
        time.sleep(1)


if __name__ == "__main__":
    main()
