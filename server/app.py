from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os
import subprocess
import re
import uuid

from agent.config import load_config
from agent.planner import QueryPlanner
from agent.state import AgentSession, AgentState
from agent.pipeline import propose_patch, revise_pending_patch
from agent.llm_router import chat as llm_chat
from agent.model_registry import list_models, set_selected
from agent.context_ingest import ingest_and_store
from rlm_wrap.store import RLMVarStore
from rlm_wrap.context import reset_context, build_minimal_meta
from mcp.registry import MCPRegistry
from mcp.policy import load_policy, load_state, save_state, is_risky_tool
from vcs.git_ops import GitOps
from patcher.staging import StagingArea
from indexer.indexer import SymbolIndexer

APP_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_ROOT / "configs" / "config.yaml"

class InitRequest(BaseModel):
    repo_root: str
    restore_remote_url: str | None = None
    allow_missing_repo: bool = False

class RestoreRemoteRequest(BaseModel):
    restore_remote_url: str
    push_on_approve: bool | None = None

class ProposeRequest(BaseModel):
    instruction: str
    mcp_confirm: str | None = None
    mcp_query: str | None = None
    context: dict | None = None

class QueryRequest(BaseModel):
    user_text: str

class ReviseRequest(BaseModel):
    instruction: str
    mcp_confirm: str | None = None
    mcp_query: str | None = None
    context: dict | None = None

class PatchRequest(BaseModel):
    unified_diff: str
    message: str = "Approved change"

class ApproveRequest(BaseModel):
    unified_diff: str
    message: str | None = None

app = FastAPI(title="Local Code Agent (MVP)")
CONFIG = load_config(CONFIG_PATH)
if not CONFIG.paths.models_dir.is_absolute():
    CONFIG.paths.models_dir = (APP_ROOT / CONFIG.paths.models_dir).resolve()
if not CONFIG.paths.index_dir.is_absolute():
    CONFIG.paths.index_dir = (APP_ROOT / CONFIG.paths.index_dir).resolve()
if not CONFIG.paths.staging_dir.is_absolute():
    CONFIG.paths.staging_dir = (APP_ROOT / CONFIG.paths.staging_dir).resolve()

MCP_CONFIG_PATH = APP_ROOT / "configs" / "mcp.yaml"
MCP_REGISTRY = MCPRegistry(MCP_CONFIG_PATH)
MCP_POLICY = load_policy(MCP_CONFIG_PATH)

STATE = {
    "repo_root": None,
    "git": None,
    "staging": None,
    "indexer": None,
    "pending_diff": None,
    "session": AgentSession(),
    "pending_summary": "",
    "pending_risk": "",
    "mcp_allowed": False,
}

@app.post("/init")
def init(req: InitRequest):
    repo = Path(req.repo_root).resolve()
    if not repo.exists():
        if req.allow_missing_repo:
            repo = (APP_ROOT / ".agent_stateless" / str(uuid.uuid4())).resolve()
            repo.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(400, f"repo_root not found: {repo}")
    ring = repo / ".agent" / "restore_ring.json"
    restore_url = req.restore_remote_url if req.restore_remote_url else CONFIG.restore.remote_url
    git = GitOps(
        repo_root=repo,
        ring_file=ring,
        restore_remote_url=restore_url or None,
        restore_remote_name=CONFIG.restore.remote_name,
        push_on_approve=CONFIG.restore.push_on_approve,
    )
    git.ensure_repo()
    staging = StagingArea(repo_root=repo, staging_root=repo / ".agent" / "staging")
    indexer = SymbolIndexer(repo_root=repo, db_path=repo / ".agent" / "index.sqlite")
    if indexer.db_path.exists():
        indexer.index_incremental()
    else:
        indexer.index_all()

    STATE.update(repo_root=str(repo), git=git, staging=staging, indexer=indexer, pending_diff=None)
    STATE["session"] = AgentSession(state=AgentState.IDLE)
    mcp_state = load_state(repo)
    STATE["mcp_allowed"] = bool(mcp_state.get("mcp_allowed", False))
    _reload_mcp_config(repo)
    minimal = build_minimal_meta(
        repo_root=repo,
        head=git.get_head(),
        model_cfg={"reasoner": CONFIG.reasoner.__dict__, "coder": CONFIG.coder.__dict__, "vlm": CONFIG.vlm.__dict__},
        index_path=indexer.db_path,
    )
    reset_context(repo, minimal)
    return {"status":"ok", "repo_root": str(repo), "restore_points": git.list_restore_points()}

@app.post("/restore_remote")
def restore_remote(req: RestoreRemoteRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    url = req.restore_remote_url.strip()
    if not url:
        STATE["git"].restore_remote_url = None
        STATE["git"]._ensure_restore_remote()
        if req.push_on_approve is not None:
            STATE["git"].push_on_approve = req.push_on_approve
        return {"status": "ok", "restore_remote_url": "", "disabled": True, "push_on_approve": STATE["git"].push_on_approve}
    # Validate remote URL
    try:
        subprocess.run(
            ["git", "ls-remote", url],
            cwd=STATE["repo_root"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError:
        STATE["git"].restore_remote_url = None
        STATE["git"]._ensure_restore_remote()
        if req.push_on_approve is not None:
            STATE["git"].push_on_approve = req.push_on_approve
        return {
            "status": "ok",
            "restore_remote_url": "",
            "disabled": True,
            "message": "invalid remote; restore disabled",
            "push_on_approve": STATE["git"].push_on_approve,
        }
    STATE["git"].restore_remote_url = url
    STATE["git"]._ensure_restore_remote()
    if req.push_on_approve is not None:
        STATE["git"].push_on_approve = req.push_on_approve
    return {"status": "ok", "restore_remote_url": url, "push_on_approve": STATE["git"].push_on_approve}

@app.post("/query")
def query(req: QueryRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    planner = QueryPlanner(STATE["session"])
    result = planner.analyze(req.user_text)
    plan = result.plan
    if result.state == "READY":
        try:
            plan = _generate_plan_llm(req.user_text)
        except Exception:
            plan = result.plan
    return {
        "state": result.state,
        "questions": result.questions,
        "plan": plan,
        "use_mcp": result.use_mcp,
        "mcp_server": result.mcp_server,
    }

@app.post("/propose")
def propose(req: ProposeRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    external_context = _context_bundle_to_text(req.context)
    mcp_query = req.mcp_query
    if not mcp_query and _should_use_mcp(req.instruction):
        mcp_query = req.instruction
    mcp_context, mcp_meta = _maybe_use_mcp(req.mcp_confirm, mcp_query)
    external_context.extend(mcp_context)
    external_context, ingest_meta = _maybe_ingest_context(req.instruction, external_context)
    try:
        proposal = propose_patch(req.instruction, STATE["indexer"], CONFIG, external_context=external_context)
    except Exception as exc:
        raise HTTPException(400, f"propose failed: {exc}")
    STATE["pending_diff"] = proposal.diff
    STATE["pending_summary"] = proposal.summary
    STATE["pending_risk"] = proposal.risk_notes
    touched = _touched_files(proposal.diff)
    return {
        "status":"ok",
        "diff": proposal.diff,
        "summary": proposal.summary,
        "touched_files": touched,
        "risk_notes": proposal.risk_notes,
        "mcp": mcp_meta,
        "ingest": ingest_meta,
    }

@app.get("/pending")
def pending():
    return {
        "pending_diff": STATE["pending_diff"],
        "summary": STATE["pending_summary"],
        "risk_notes": STATE["pending_risk"],
    }

@app.post("/revise_pending")
def revise_pending(req: ReviseRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if not STATE["pending_diff"]:
        raise HTTPException(400, "no pending diff")
    external_context = _context_bundle_to_text(req.context)
    mcp_query = req.mcp_query
    if not mcp_query and _should_use_mcp(req.instruction):
        mcp_query = req.instruction
    mcp_context, mcp_meta = _maybe_use_mcp(req.mcp_confirm, mcp_query)
    external_context.extend(mcp_context)
    external_context, ingest_meta = _maybe_ingest_context(req.instruction, external_context)
    try:
        proposal = revise_pending_patch(req.instruction, STATE["pending_diff"], STATE["indexer"], CONFIG, external_context=external_context)
    except Exception as exc:
        raise HTTPException(400, f"revise failed: {exc}")
    STATE["pending_diff"] = proposal.diff
    STATE["pending_summary"] = proposal.summary
    STATE["pending_risk"] = proposal.risk_notes
    touched = _touched_files(proposal.diff)
    return {
        "status":"ok",
        "diff": proposal.diff,
        "summary": proposal.summary,
        "touched_files": touched,
        "risk_notes": proposal.risk_notes,
        "mcp": mcp_meta,
        "ingest": ingest_meta,
    }

@app.post("/apply_to_staging")
def apply_to_staging(req: PatchRequest):
    if STATE["staging"] is None:
        raise HTTPException(400, "init first")
    STATE["staging"].reset()
    STATE["staging"].apply_unified_diff(req.unified_diff)
    STATE["pending_diff"] = req.unified_diff
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    return {"status":"ok"}


def _touched_files(unified_diff: str) -> list[str]:
    files = []
    for line in unified_diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:].strip())
    return files


def _context_bundle_to_text(context: dict | None) -> list[str]:
    if not context:
        return []
    blocks: list[str] = []
    files = context.get("files") or []
    for f in files:
        path = f.get("path", "unknown")
        content = f.get("content", "")
        if content:
            blocks.append(f"[File] {path}\n{content}")
    snippets = context.get("snippets") or []
    for s in snippets:
        path = s.get("path", "unknown")
        start = s.get("startLine", "?")
        end = s.get("endLine", "?")
        text = s.get("text", "")
        if text:
            blocks.append(f"[Snippet] {path}:{start}-{end}\n{text}")
    return blocks


def _maybe_ingest_context(user_text: str, blocks: list[str]) -> tuple[list[str], dict]:
    if not blocks:
        return blocks, {"used": False}
    cfg = CONFIG.context_ingest
    if not cfg.enabled:
        return blocks, {"used": False}
    joined = "\n\n".join(blocks)
    if len(joined) <= cfg.max_chars:
        return blocks, {"used": False, "reason": "below_threshold"}
    store = RLMVarStore(repo_root=Path(STATE["repo_root"]))
    result = ingest_and_store(
        joined,
        query=user_text,
        store=store,
        chunk_size=cfg.chunk_size,
        chunk_overlap=cfg.chunk_overlap,
        top_k=cfg.top_k,
    )
    out = [f"[Ingest Summary]\n{result.summary}"]
    for idx, ch in enumerate(result.top_chunks, start=1):
        out.append(f"[Ingest Chunk {idx}]\n{ch}")
    meta = {
        "used": True,
        "chunks": len(result.chunks),
        "top_k": len(result.top_chunks),
        "summary": result.summary,
    }
    return out, meta


def _generate_plan_llm(user_text: str) -> list[str]:
    prompt = (
        "Create a short, concrete plan (3-5 steps) for the following request. "
        "Return as a bullet list, each line starting with '- '.\n\n"
        f"Request: {user_text}"
    )
    raw = llm_chat("reasoner", [
        {"role": "system", "content": "You are a software planning assistant."},
        {"role": "user", "content": prompt},
    ], CONFIG, Path(STATE["repo_root"]), CONFIG_PATH)
    lines = [l.strip("- ") for l in raw.splitlines() if l.strip().startswith("-")]
    return lines[:5] if lines else [\"Review request\", \"Identify relevant files\", \"Propose minimal changes\"]


def _maybe_use_mcp(confirm: str | None, query: str | None) -> tuple[list[str], dict]:
    meta = {"used": False, "server": None, "error": None}
    if not query:
        return [], meta
    allow = False
    if confirm and confirm.strip().upper() == "YES":
        allow = True
        STATE["mcp_allowed"] = True
        save_state(Path(STATE["repo_root"]), {"mcp_allowed": True})
    elif STATE.get("mcp_allowed", False):
        allow = True
    if not allow:
        meta["error"] = "MCP not allowed (call /mcp/allow with confirm YES)"
        return [], meta
    try:
        server = "playwright"
        client = MCP_REGISTRY.get_client(server)
        tools_resp = client.list_tools()
        tools = _extract_tools(tools_resp)
        external = _run_mcp_query(client, tools, query)
        meta["used"] = bool(external)
        meta["server"] = server
        return external, meta
    except Exception as exc:
        meta["error"] = str(exc)
        return [], meta


def _extract_tools(resp: dict) -> list[dict]:
    if "result" in resp and isinstance(resp["result"], dict):
        tools = resp["result"].get("tools")
        if isinstance(tools, list):
            return tools
    if "tools" in resp and isinstance(resp["tools"], list):
        return resp["tools"]
    return []


def _run_mcp_query(client, tools: list[dict], query: str) -> list[str]:
    out: list[str] = []
    query = query.strip()
    # Prefer search-like tool
    search_tool = _find_tool(tools, field="query", name_contains=["search"])
    if search_tool:
        resp = client.call_tool(search_tool["name"], {"query": query})
        out.append(_stringify_response(resp, limit=4000))
        return out
    # Fallback: navigate to URL then fetch content if possible
    if _looks_like_url(query):
        # Domain allowlist enforcement
        if MCP_POLICY.allowed_domains:
            domain = _domain_from_url(query)
            if domain and not _domain_allowed(domain, MCP_POLICY.allowed_domains):
                return out
        nav_tool = _find_tool(tools, field="url", name_contains=["navigate", "goto", "open", "visit"])
        if nav_tool:
            client.call_tool(nav_tool["name"], {"url": query})
            content_tool = _find_tool(tools, field=None, name_contains=["content", "text", "extract", "page"])
            if content_tool:
                args = _build_empty_args(content_tool)
                resp = client.call_tool(content_tool["name"], args)
                out.append(_stringify_response(resp, limit=4000))
                return out
    return out


def _find_tool(tools: list[dict], field: str | None, name_contains: list[str]) -> dict | None:
    for t in tools:
        name = (t.get("name") or "").lower()
        if not any(k in name for k in name_contains):
            continue
        if field is None:
            return t
        schema = t.get("inputSchema") or {}
        props = (schema.get("properties") or {})
        if field in props:
            return t
    return None


def _build_empty_args(tool: dict) -> dict:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    # If selector is allowed, default to body to get full text
    if "selector" in props:
        return {"selector": "body"}
    return {}


def _stringify_response(resp: dict, limit: int = 4000) -> str:
    raw = str(resp)
    if len(raw) > limit:
        return raw[:limit] + "...(truncated)"
    return raw


def _looks_like_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text))

def _should_use_mcp(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ["browse", "browser", "web", "search", "google", "brave", "playwright", "website", "link", "url"])

def _domain_from_url(url: str) -> str | None:
    m = re.match(r"^https?://([^/]+)", url.strip())
    if not m:
        return None
    return m.group(1).lower()

def _domain_allowed(domain: str, allowlist: list[str]) -> bool:
    for allowed in allowlist:
        allowed = allowed.lower()
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def _reload_mcp_config(repo_root: Path) -> None:
    global MCP_POLICY
    override = repo_root / "mcp.json"
    if override.exists():
        MCP_REGISTRY.set_config_path(override)
    else:
        MCP_REGISTRY.set_config_path(MCP_CONFIG_PATH)
    MCP_REGISTRY.reload()
    MCP_POLICY = load_policy(MCP_REGISTRY.config_path)

@app.post("/approve")
def approve(req: ApproveRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if STATE["pending_diff"] is None:
        raise HTTPException(400, "no pending diff")
    if STATE["git"].status_dirty():
        raise HTTPException(409, "repo has uncommitted changes; please clean or revert before approval")
    if req.unified_diff != STATE["pending_diff"]:
        raise HTTPException(400, "approved diff does not match pending diff")
    try:
        STATE["staging"].check_unified_diff(req.unified_diff)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    try:
        STATE["staging"].apply_unified_diff_to_repo(req.unified_diff)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    message = req.message
    if not message or message == "Approved change":
        message = STATE["git"].commit_message_from_diff(req.unified_diff, fallback="Approved change")
    sha = STATE["git"].commit_approved(message)
    push_ok = None
    push_msg = ""
    if STATE["git"].push_on_approve:
        push_ok, push_msg = STATE["git"].push_head()
    # Re-index (MVP full re-index; later incremental)
    STATE["indexer"].index_all()
    # Clear pending context + reset RLM vars
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    minimal = build_minimal_meta(
        repo_root=Path(STATE["repo_root"]),
        head=sha,
        model_cfg={"reasoner": CONFIG.reasoner.__dict__, "coder": CONFIG.coder.__dict__, "vlm": CONFIG.vlm.__dict__},
        index_path=STATE["indexer"].db_path,
    )
    reset_context(Path(STATE["repo_root"]), minimal)
    return {
        "status":"ok",
        "commit": sha,
        "restore_points": STATE["git"].list_restore_points(),
        "restore_push": {"ok": push_ok, "message": push_msg},
    }

@app.post("/reject")
def reject():
    if STATE["staging"] is None:
        raise HTTPException(400, "init first")
    STATE["staging"].reset()
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
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
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    return {"status":"ok", "head": req.sha}

@app.post("/reset_context")
def reset_context_endpoint():
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    head = STATE["git"].get_head()
    minimal = build_minimal_meta(
        repo_root=Path(STATE["repo_root"]),
        head=head,
        model_cfg={"reasoner": CONFIG.reasoner.__dict__, "coder": CONFIG.coder.__dict__, "vlm": CONFIG.vlm.__dict__},
        index_path=STATE["indexer"].db_path,
    )
    reset_context(Path(STATE["repo_root"]), minimal)
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    STATE["session"] = AgentSession(state=AgentState.IDLE)
    return {"status": "ok"}

@app.get("/models")
def get_models():
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    repo_root = Path(STATE["repo_root"])
    return {
        "reasoner": list_models("reasoner", CONFIG, repo_root, CONFIG_PATH),
        "coder": list_models("coder", CONFIG, repo_root, CONFIG_PATH),
    }

@app.post("/models/select")
def select_model(req: ModelSelectRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.role not in ("reasoner", "coder"):
        raise HTTPException(400, "role must be reasoner or coder")
    set_selected(req.role, req.model_id, Path(STATE["repo_root"]), CONFIG_PATH)
    return {"status": "ok"}

class RunCommandRequest(BaseModel):
    command: str
    require_yes: bool = True
    confirm: str | None = None

class MCPListRequest(BaseModel):
    server: str
    confirm: str | None = None

class MCPCallRequest(BaseModel):
    server: str
    tool: str
    arguments: dict
    confirm: str | None = None

class MCPAllowRequest(BaseModel):
    confirm: str | None = None

class MCPRevokeRequest(BaseModel):
    confirm: str | None = None

class ModelSelectRequest(BaseModel):
    role: str
    model_id: str

class MCPStatusResponse(BaseModel):
    mcp_allowed: bool
    allowed_domains: list[str]

@app.post("/run_command")
def run_command(req: RunCommandRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.require_yes and (req.confirm is None or req.confirm.strip().upper() != "YES"):
        return {
            "status": "needs_confirmation",
            "message": "Command requires explicit YES confirmation.",
        }
    try:
        p = subprocess.run(
            req.command,
            shell=True,
            cwd=STATE["repo_root"],
            text=True,
            capture_output=True,
        )
    except Exception as exc:
        raise HTTPException(400, f"command failed: {exc}")
    return {
        "status": "ok",
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }

@app.post("/mcp/allow")
def mcp_allow(req: MCPAllowRequest):
    if req.confirm is None or req.confirm.strip().upper() != "YES":
        return {"status": "needs_confirmation", "message": "MCP access requires explicit YES confirmation."}
    STATE["mcp_allowed"] = True
    if STATE["repo_root"] is not None:
        save_state(Path(STATE["repo_root"]), {"mcp_allowed": True})
    return {"status": "ok", "mcp_allowed": True}

@app.post("/mcp/revoke")
def mcp_revoke(req: MCPRevokeRequest):
    if req.confirm is None or req.confirm.strip().upper() != "YES":
        return {"status": "needs_confirmation", "message": "MCP revoke requires explicit YES confirmation."}
    STATE["mcp_allowed"] = False
    if STATE["repo_root"] is not None:
        save_state(Path(STATE["repo_root"]), {"mcp_allowed": False})
    return {"status": "ok", "mcp_allowed": False}

@app.get("/mcp/status")
def mcp_status():
    return {
        "mcp_allowed": bool(STATE.get("mcp_allowed", False)),
        "allowed_domains": MCP_POLICY.allowed_domains,
        "repo_root": STATE.get("repo_root"),
        "servers": list(MCP_REGISTRY.load().keys()) if MCP_REGISTRY.config_path.exists() else [],
        "config_path": str(MCP_REGISTRY.config_path),
    }

@app.post("/mcp/list_tools")
def mcp_list_tools(req: MCPListRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.confirm is None or req.confirm.strip().upper() != "YES":
        return {"status": "needs_confirmation", "message": "Starting MCP server requires YES confirmation."}
    try:
        STATE["mcp_allowed"] = True
        if STATE["repo_root"] is not None:
            save_state(Path(STATE["repo_root"]), {"mcp_allowed": True})
        client = MCP_REGISTRY.get_client(req.server)
        resp = client.list_tools()
        return {"status": "ok", "response": resp}
    except Exception as exc:
        raise HTTPException(400, f"mcp list_tools failed: {exc}")

@app.post("/mcp/call")
def mcp_call(req: MCPCallRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.confirm is None or req.confirm.strip().upper() != "YES":
        # If tool is risky, require explicit YES
        risky, reason = is_risky_tool(req.tool, req.arguments, Path(STATE["repo_root"]), MCP_POLICY)
        if risky:
            return {"status": "needs_confirmation", "message": f"Tool call requires YES confirmation: {reason}"}
        if not STATE.get("mcp_allowed", False):
            return {"status": "needs_confirmation", "message": "MCP access requires explicit YES confirmation."}
    try:
        STATE["mcp_allowed"] = True
        if STATE["repo_root"] is not None:
            save_state(Path(STATE["repo_root"]), {"mcp_allowed": True})
        client = MCP_REGISTRY.get_client(req.server)
        resp = client.call_tool(req.tool, req.arguments)
        return {"status": "ok", "response": resp}
    except Exception as exc:
        raise HTTPException(400, f"mcp call failed: {exc}")

@app.post("/mcp/reload")
def mcp_reload():
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    _reload_mcp_config(Path(STATE["repo_root"]))
    return {"status": "ok", "config_path": str(MCP_REGISTRY.config_path)}
