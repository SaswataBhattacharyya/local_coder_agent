from pathlib import Path
from agent.state_store import AgentStateStore


def test_branch_switch_pending(tmp_path: Path):
    store = AgentStateStore(repo_root=tmp_path, session_id="s1")
    store.ensure_session("main")
    store.write_pending_patch({"diff": "A"})
    assert store.read_pending_patch().get("diff") == "A"

    store.switch_branch("branch_a")
    assert store.read_pending_patch().get("diff") is None or store.read_pending_patch().get("diff") == ""

    store.write_pending_patch({"diff": "B"})
    assert store.read_pending_patch().get("diff") == "B"

    store.switch_branch("main")
    assert store.read_pending_patch().get("diff") == "A"


def test_snapshot_restore(tmp_path: Path):
    store = AgentStateStore(repo_root=tmp_path, session_id="s2")
    store.ensure_session("main")
    store.write_pending_patch({"diff": "X"})
    snap = store.snapshot("sha123", message="test")

    store.write_pending_patch({"diff": "Y"})
    assert store.read_pending_patch().get("diff") == "Y"

    store.restore_snapshot(snap)
    assert store.read_pending_patch().get("diff") == "X"
