from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os

from agent.config import load_config
from agent.planner import QueryPlanner
from agent.state import AgentSession, AgentState
from vcs.git_ops import GitOps
from patcher.staging import StagingArea
from indexer.indexer import SymbolIndexer

APP_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_ROOT / "configs" / "config.yaml"

class InitRequest(BaseModel):
    repo_root: str

class ProposeRequest(BaseModel):
    instruction: str

class QueryRequest(BaseModel):
    user_text: str

class PatchRequest(BaseModel):
    unified_diff: str
    message: str = "Approved change"

app = FastAPI(title="Local Code Agent (MVP)")

STATE = {
    "repo_root": None,
    "git": None,
    "staging": None,
    "indexer": None,
    "pending_diff": None,
    "session": AgentSession(),
}

@app.post("/init")
def init(req: InitRequest):
    repo = Path(req.repo_root).resolve()
    if not repo.exists():
        raise HTTPException(400, f"repo_root not found: {repo}")
    ring = repo / ".agent" / "restore_ring.json"
    git = GitOps(repo_root=repo, ring_file=ring)
    git.ensure_repo()
    staging = StagingArea(repo_root=repo, staging_root=repo / ".agent" / "staging")
    indexer = SymbolIndexer(repo_root=repo, db_path=repo / ".agent" / "index.sqlite")
    if indexer.db_path.exists():
        indexer.index_incremental()
    else:
        indexer.index_all()

    STATE.update(repo_root=str(repo), git=git, staging=staging, indexer=indexer, pending_diff=None)
    STATE["session"] = AgentSession(state=AgentState.IDLE)
    return {"status":"ok", "repo_root": str(repo), "restore_points": git.list_restore_points()}

@app.post("/query")
def query(req: QueryRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    planner = QueryPlanner(STATE["session"])
    result = planner.analyze(req.user_text)
    return {"state": result.state, "questions": result.questions, "plan": result.plan}

@app.post("/propose")
def propose(req: ProposeRequest):
    # MVP: placeholder. In next milestone, call reasoner->retrieve->coder->diff
    STATE["pending_diff"] = f"""diff --git a/README.md b/README.md
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/README.md
@@ -0,0 +1 @@
+TODO: generated patch placeholder for instruction: {req.instruction}
"""
    return {"status":"ok", "pending_diff": STATE["pending_diff"]}

@app.get("/pending")
def pending():
    return {"pending_diff": STATE["pending_diff"]}

@app.post("/apply_to_staging")
def apply_to_staging(req: PatchRequest):
    if STATE["staging"] is None:
        raise HTTPException(400, "init first")
    STATE["staging"].reset()
    STATE["staging"].apply_unified_diff(req.unified_diff)
    STATE["pending_diff"] = req.unified_diff
    return {"status":"ok"}

@app.post("/approve")
def approve(req: PatchRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    # Apply diff to real repo then commit (simplest MVP path)
    import subprocess
    p = subprocess.run(["git","apply","--whitespace=nowarn","-"], input=req.unified_diff, text=True, cwd=STATE["repo_root"], capture_output=True)
    if p.returncode != 0:
        raise HTTPException(400, f"git apply failed: {p.stderr.strip()}")
    sha = STATE["git"].commit_approved(req.message)
    # Re-index (MVP full re-index; later incremental)
    STATE["indexer"].index_all()
    # Clear pending context
    STATE["pending_diff"] = None
    return {"status":"ok", "commit": sha, "restore_points": STATE["git"].list_restore_points()}

@app.post("/reject")
def reject():
    if STATE["staging"] is None:
        raise HTTPException(400, "init first")
    STATE["staging"].reset()
    STATE["pending_diff"] = None
    return {"status":"ok"}

@app.get("/restore_points")
def restore_points():
    if STATE["git"] is None:
        raise HTTPException(400, "init first")
    return {"restore_points": STATE["git"].list_restore_points()}

class RevertRequest(BaseModel):
    sha: str

@app.post("/revert")
def revert(req: RevertRequest):
    if STATE["git"] is None:
        raise HTTPException(400, "init first")
    STATE["git"].hard_reset_to(req.sha)
    STATE["indexer"].index_all()
    STATE["pending_diff"] = None
    return {"status":"ok", "head": req.sha}
