from pathlib import Path
from server.tasks import TaskQueue


def test_task_submit_and_status(tmp_path: Path):
    q = TaskQueue(tmp_path)
    tid = q.submit("QUERY", {"user_text": "hello"})
    status = q.status(tid)
    assert status["status"] == "queued"
