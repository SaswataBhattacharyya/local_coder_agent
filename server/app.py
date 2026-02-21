from __future__ import annotations
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pathlib import Path
import os
import subprocess
import re
import uuid
import json
import time
import sqlite3
import shutil

class TraceSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.start = time.perf_counter()
        self.end = None

    def finish(self) -> None:
        self.end = time.perf_counter()

    def ms(self) -> float:
        if self.end is None:
            return 0.0
        return (self.end - self.start) * 1000.0


class TraceContext:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.spans: list[TraceSpan] = []

    def span(self, name: str) -> TraceSpan:
        s = TraceSpan(name)
        self.spans.append(s)
        return s

    def to_dict(self) -> dict:
        return {
            "trace_id": self.id,
            "spans": [
                {"name": s.name, "ms": round(s.ms(), 2)}
                for s in self.spans
                if s.end is not None
            ],
        }

    def log(self, prefix: str) -> None:
        parts = [f"{s.name}={s.ms():.2f}ms" for s in self.spans if s.end is not None]
        print(f"[trace {self.id}] {prefix}: " + ", ".join(parts))

from agent.config import load_config
from agent.planner import QueryPlanner
from agent.info_pipeline import generate_info_answer, generate_info_answer_from_context
from agent.state import AgentSession, AgentState
from agent.pipeline import propose_patch, revise_pending_patch
from agent.llm_router import chat as llm_chat, chat_with_images
from agent.model_registry import list_models, set_selected
from agent.context_ingest import ingest_and_store
from rlm_wrap.store import RLMVarStore
from agent.state_store import AgentStateStore
from indexer.dep_graph import DependencyGraph
from indexer.repo_map import RepoMapBuilder
from server.tasks import TaskQueue, TaskWorker
from rlm_wrap.context import reset_context, build_minimal_meta
from mcp.registry import MCPRegistry
from mcp.policy import load_policy, load_state, save_state
from vcs.snapshot_cache import SnapshotCache
from patcher.staging import StagingArea
from indexer.indexer import SymbolIndexer

APP_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_ROOT / "configs" / "config.yaml"

class InitRequest(BaseModel):
    repo_root: str
    allow_missing_repo: bool = False

class RestoreRemoteRequest(BaseModel):
    restore_remote_url: str
    push_on_approve: bool | None = None

class ProposeRequest(BaseModel):
    instruction: str
    mcp_confirm: str | None = None
    mcp_query: str | None = None
    context: dict | None = None
    workspace_context: dict | None = None

class QueryRequest(BaseModel):
    user_text: str
    workspace_context: dict | None = None
    images: list[dict] | None = None

class ReviseRequest(BaseModel):
    instruction: str
    mcp_confirm: str | None = None
    mcp_query: str | None = None
    context: dict | None = None
    workspace_context: dict | None = None

class PatchRequest(BaseModel):
    unified_diff: str
    message: str = "Approved change"

class ApproveRequest(BaseModel):
    unified_diff: str
    message: str | None = None

class ModelSelectRequest(BaseModel):
    role: str
    model_id: str

class ModelAddRequest(BaseModel):
    role: str
    model_id: str
    repo_id: str
    filename_hint: str | None = None
    context: int | None = None
    model_dir: str | None = None
    download_now: bool | None = None

class ModelRemoveRequest(BaseModel):
    role: str
    model_ids: list[str]

class SessionStartRequest(BaseModel):
    repo_root: str

class BranchCreateRequest(BaseModel):
    name: str
    from_branch: str | None = None

class BranchSwitchRequest(BaseModel):
    name: str

class SnapshotRequest(BaseModel):
    message: str | None = None

class SnapshotRestoreRequest(BaseModel):
    snapshot_id: str

class RepoMapRebuildRequest(BaseModel):
    full: bool = False

class TaskSubmitRequest(BaseModel):
    type: str
    payload: dict

class TaskStatusRequest(BaseModel):
    task_id: str

class TaskCancelRequest(BaseModel):
    task_id: str

class TaskLogsRequest(BaseModel):
    task_id: str
    after: float | None = None

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
    "snapshots": None,
    "staging": None,
    "indexer": None,
    "pending_diff": None,
    "session": AgentSession(),
    "pending_summary": "",
    "pending_risk": "",
    "mcp_allowed": False,
    "session_id": None,
    "state_store": None,
    "dep_graph": None,
    "task_queue": None,
    "task_worker": None,
    "suggest_next_steps": True,
}


def _update_next_steps_flag(user_text: str) -> None:
    text = (user_text or "").lower()
    stop_tokens = [
        "stop suggestions", "disable suggestions", "turn off suggestions", "no suggestions",
        "stop next steps", "disable next steps", "turn off next steps",
        "stop improvements", "disable improvements", "turn off improvements",
    ]
    enable_tokens = [
        "enable suggestions", "turn on suggestions", "resume suggestions",
        "enable next steps", "turn on next steps", "resume next steps",
        "enable improvements", "turn on improvements", "resume improvements",
    ]
    if any(t in text for t in stop_tokens):
        STATE["suggest_next_steps"] = False
    elif any(t in text for t in enable_tokens):
        STATE["suggest_next_steps"] = True


def _sse_event(event: str, data: str) -> str:
    data = data.replace("\r", "")
    lines = data.split("\n")
    payload = "".join([f"data: {line}\n" for line in lines])
    return f"event: {event}\n{payload}\n"


def _stream_text(event: str, text: str, chunk: int = 800):
    for i in range(0, len(text), chunk):
        yield _sse_event(event, text[i:i + chunk])


def _handle_query(req: QueryRequest, trace: TraceContext) -> dict:
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    images = req.images or []
    user_text = req.user_text
    _update_next_steps_flag(user_text)
    if images:
        try:
            span = trace.span("vlm_analyze")
            analysis = chat_with_images("vlm", [
                {"role": "system", "content": "You are a vision assistant. Describe the image(s) concisely and accurately."},
                {"role": "user", "content": user_text},
            ], images, CONFIG, Path(STATE["repo_root"]), CONFIG_PATH)
            span.finish()
            if analysis:
                refine_prompt = (
                    "Given the user request and image analysis, rewrite a refined, specific instruction for the planner. "
                    "Keep it short and concrete, include any critical visual details. Return only the refined instruction."
                )
                span = trace.span("refine_prompt")
                refined = llm_chat("reasoner", [
                    {"role": "system", "content": "You refine requests for planning."},
                    {"role": "user", "content": f"User request:\n{user_text}\n\nImage analysis:\n{analysis}\n\n{refine_prompt}"},
                ], CONFIG, Path(STATE["repo_root"]), CONFIG_PATH)
                span.finish()
                user_text = refined.strip() or f"{user_text}\n\n[Image Analysis]\n{analysis}"
        except Exception as exc:
            user_text = f"{user_text}\n\n[Image Analysis Error]\n{exc}"
    planner = QueryPlanner(STATE["session"])
    span = trace.span("intent_router")
    result = planner.analyze(
        user_text,
        repo_root_known=STATE["repo_root"] is not None,
        has_pending_patch=STATE.get("pending_diff") is not None,
    )
    span.finish()
    if result.intent in ("EDIT", "COMMAND"):
        span = trace.span("repomap_refresh")
        _ensure_repo_map()
        span.finish()
    plan = result.plan
    if result.state == "READY" and result.intent not in ("INFO",):
        try:
            span = trace.span("llm_plan")
            plan = _generate_plan_llm(user_text)
            span.finish()
        except Exception:
            plan = result.plan
    answer = None
    if result.state == "READY" and result.intent == "INFO":
        try:
            workspace_ctx = req.workspace_context
            if workspace_ctx:
                span = trace.span("info_pipeline")
                info = generate_info_answer_from_context(workspace_ctx)
                answer = info.render(include_next_steps=bool(STATE.get("suggest_next_steps", True)))
                span.finish()
            else:
                repo_root = str(STATE["repo_root"])
                if ".agent_stateless" in repo_root:
                    answer = (
                        "No workspace context received. The server cannot access your local VS Code files. "
                        "Enable 'sendContextBundle' or init with a real repo_root inside the VM."
                    )
                else:
                    span = trace.span("repomap_refresh")
                    _ensure_repo_map()
                    span.finish()
                    span = trace.span("info_pipeline")
                    info = generate_info_answer(Path(STATE["repo_root"]))
                    answer = info.render(include_next_steps=bool(STATE.get("suggest_next_steps", True)))
                    span.finish()
        except Exception as exc:
            answer = f"Unable to generate summary: {exc}"
    return {
        "state": result.state,
        "questions": result.questions,
        "plan": plan,
        "answer": answer,
        "trace": trace.to_dict(),
        "use_mcp": result.use_mcp,
        "mcp_server": result.mcp_server,
        "intent": result.intent,
        "needs_confirm": result.needs_confirm,
        "confirm_token": result.confirm_token,
    }


def _context_to_text(ctx: dict) -> str:
    parts: list[str] = []
    ws = ctx.get("workspaceName")
    if ws:
        parts.append(f"Workspace: {ws}")
    tree = ctx.get("tree") or []
    if tree:
        names = [t.get("name") for t in tree if isinstance(t, dict) and t.get("name")]
        parts.append("Top-level: " + ", ".join(names[:50]))
    files = ctx.get("files") or []
    for f in files:
        path = f.get("path") or "unknown"
        content = (f.get("content") or "")[:6000]
        parts.append(f"File: {Path(path).name}\n{content}")
    scripts = ctx.get("packageScripts") or {}
    if scripts:
        parts.append("package.json scripts:\n" + "\n".join([f"{k}: {v}" for k, v in scripts.items()]))
    return "\n\n".join(parts)


def _llm_info_answer_with_continuation(ctx: dict, max_parts: int = 3) -> list[str]:
    context_text = _context_to_text(ctx)
    extra_section = ""
    if bool(STATE.get("suggest_next_steps", True)):
        extra_section = "5) Next Steps / Improvements (short, actionable)\n"
    base_prompt = (
        "You are summarizing a codebase from partial context. "
        "Write a crisp but descriptive answer with these sections:\n"
        "1) Project Summary\n"
        "2) How to Start (2-3 likely commands)\n"
        "3) Prerequisites/Notes\n"
        "4) Ports (if found)\n"
        f"{extra_section}\n"
        "If you are not finished, end with <CONTINUE>. If complete, end with <END>.\n\n"
        f"Context:\n{context_text}"
    )
    parts: list[str] = []
    raw = llm_chat("reasoner", [
        {"role": "system", "content": "You are a software project summarizer."},
        {"role": "user", "content": base_prompt},
    ], CONFIG, Path(STATE["repo_root"]), CONFIG_PATH)
    parts.append(raw)
    while len(parts) < max_parts and "<CONTINUE>" in raw and "<END>" not in raw:
        raw = llm_chat("reasoner", [
            {"role": "system", "content": "Continue the previous summary. Do not repeat. End with <CONTINUE> or <END>."},
            {"role": "assistant", "content": "\n".join(parts)},
            {"role": "user", "content": "Continue."},
        ], CONFIG, Path(STATE["repo_root"]), CONFIG_PATH)
        parts.append(raw)
    return parts


def _llm_continue_text(parts: list[str], max_parts: int = 3) -> list[str]:
    out = list(parts)
    last = parts[-1] if parts else ""
    while len(out) < max_parts and "<CONTINUE>" in last and "<END>" not in last:
        last = llm_chat("reasoner", [
            {"role": "system", "content": "Continue the previous response. Do not repeat. End with <CONTINUE> or <END>."},
            {"role": "assistant", "content": "\n".join(out)},
            {"role": "user", "content": "Continue."},
        ], CONFIG, Path(STATE["repo_root"]), CONFIG_PATH)
        out.append(last)
    return out


def _needs_continuation(text: str, max_stream: int) -> bool:
    if len(text) >= max_stream:
        return True
    tail = text.strip()
    if not tail:
        return False
    return not tail.endswith((".", "!", "?", "`", ")"))

@app.post("/init")
def init(req: InitRequest):
    repo = Path(req.repo_root).resolve()
    if not repo.exists():
        if req.allow_missing_repo:
            # Reuse a stable stateless repo to avoid repeated git init/commit on each /init
            if STATE.get("repo_root") and str(STATE.get("repo_root")).endswith(".agent_stateless/default"):
                repo = Path(STATE["repo_root"]).resolve()
            else:
                repo = (APP_ROOT / ".agent_stateless" / "default").resolve()
                repo.mkdir(parents=True, exist_ok=True)
        else:
            raise HTTPException(400, f"repo_root not found: {repo}")
    snapshots = SnapshotCache(repo_root=repo, max_snapshots=4)
    staging = StagingArea(repo_root=repo, staging_root=repo / ".agent" / "staging")
    indexer = SymbolIndexer(repo_root=repo, db_path=repo / ".agent" / "index.sqlite")
    dep_graph = DependencyGraph(repo_root=repo, db_path=repo / ".agent" / "deps.sqlite")
    dep_graph.init_db()
    if indexer.db_path.exists():
        indexer.index_incremental()
    else:
        indexer.index_all()

    STATE.update(repo_root=str(repo), snapshots=snapshots, staging=staging, indexer=indexer, pending_diff=None, dep_graph=dep_graph)
    STATE["session"] = AgentSession(state=AgentState.IDLE)
    mcp_state = load_state(repo)
    STATE["mcp_allowed"] = bool(mcp_state.get("mcp_allowed", False))
    _reload_mcp_config(repo)
    minimal = build_minimal_meta(
        repo_root=repo,
        head=snapshots.get_head(),
        model_cfg={"reasoner": CONFIG.reasoner.__dict__, "coder": CONFIG.coder.__dict__, "vlm": CONFIG.vlm.__dict__},
        index_path=indexer.db_path,
    )
    reset_context(repo, minimal)
    STATE["session_id"] = "default"
    store = AgentStateStore(repo_root=repo, session_id="default")
    store.ensure_session("main")
    STATE["state_store"] = store
    STATE["task_queue"] = TaskQueue(repo)
    if STATE.get("task_worker") is None or not STATE["task_worker"].is_alive():
        worker = TaskWorker(STATE["task_queue"], _handle_task)
        worker.start()
        STATE["task_worker"] = worker
    return {"status":"ok", "repo_root": str(repo), "snapshots": snapshots.list_snapshots()}

@app.post("/query")
def query(req: QueryRequest, response: Response):
    trace = TraceContext()
    result = _handle_query(req, trace)
    response.headers["X-Trace-Id"] = trace.id
    trace.log("query")
    return result


@app.post("/query_stream")
def query_stream(req: QueryRequest):
    trace = TraceContext()
    result = _handle_query(req, trace)
    max_stream = 4000

    def gen():
        yield _sse_event("status", "started")
        if result.get("intent") == "INFO" and req.workspace_context and (
            not result.get("answer") or len(result.get("answer") or "") > max_stream
        ):
            span = trace.span("llm_info_stream")
            parts = _llm_info_answer_with_continuation(req.workspace_context, max_parts=3)
            span.finish()
            for part in parts:
                clean = part.replace("<CONTINUE>", "").replace("<END>", "")
                yield from _stream_text("answer", clean)
        elif result.get("answer"):
            text = result["answer"]
            if "<CONTINUE>" in text or "<END>" in text or _needs_continuation(text, max_stream):
                parts = _llm_continue_text([text], max_parts=3)
                for part in parts:
                    clean = part.replace("<CONTINUE>", "").replace("<END>", "")
                    yield from _stream_text("answer", clean)
            else:
                yield from _stream_text("answer", text)
        elif result.get("questions"):
            yield from _stream_text("questions", "\n".join(result["questions"]))
        elif result.get("plan"):
            yield from _stream_text("plan", "\n".join(f"- {p}" for p in result["plan"]))
        else:
            yield _sse_event("error", "No answer or plan was produced.")
        yield _sse_event("done", json.dumps(result.get("trace", {})))

    response = StreamingResponse(gen(), media_type="text/event-stream")
    response.headers["X-Trace-Id"] = trace.id
    trace.log("query_stream")
    return response

@app.post("/propose")
def propose(req: ProposeRequest, response: Response):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    trace = TraceContext()
    span = trace.span("repomap_refresh")
    _ensure_repo_map()
    span.finish()
    external_context = _context_bundle_to_text(req.context)
    mcp_query = req.mcp_query
    if not mcp_query and _should_use_mcp(req.instruction):
        mcp_query = req.instruction
    span = trace.span("mcp")
    mcp_context, mcp_meta = _maybe_use_mcp(req.mcp_confirm, mcp_query)
    span.finish()
    external_context.extend(mcp_context)
    span = trace.span("context_ingest")
    external_context, ingest_meta = _maybe_ingest_context(req.instruction, external_context)
    span.finish()
    try:
        span = trace.span("llm_propose")
        proposal = propose_patch(req.instruction, STATE["indexer"], CONFIG, external_context=external_context)
        span.finish()
    except Exception as exc:
        raise HTTPException(400, f"propose failed: {exc}")
    STATE["pending_diff"] = proposal.diff
    STATE["pending_summary"] = proposal.summary
    STATE["pending_risk"] = proposal.risk_notes
    if STATE.get("state_store"):
        STATE["state_store"].write_pending_patch({
            "diff": proposal.diff,
            "summary": proposal.summary,
            "risk": proposal.risk_notes,
        })
    touched = _touched_files(proposal.diff)
    response.headers["X-Trace-Id"] = trace.id
    trace.log("propose")
    return {
        "status":"ok",
        "diff": proposal.diff,
        "summary": proposal.summary,
        "touched_files": touched,
        "risk_notes": proposal.risk_notes,
        "mcp": mcp_meta,
        "ingest": ingest_meta,
        "trace": trace.to_dict(),
    }


@app.post("/propose_stream")
def propose_stream(req: ProposeRequest):
    trace = TraceContext()
    span = trace.span("repomap_refresh")
    _ensure_repo_map()
    span.finish()
    external_context = _context_bundle_to_text(req.context)
    mcp_query = req.mcp_query
    if not mcp_query and _should_use_mcp(req.instruction):
        mcp_query = req.instruction
    span = trace.span("mcp")
    mcp_context, mcp_meta = _maybe_use_mcp(req.mcp_confirm, mcp_query)
    span.finish()
    external_context.extend(mcp_context)
    span = trace.span("context_ingest")
    external_context, ingest_meta = _maybe_ingest_context(req.instruction, external_context)
    span.finish()
    try:
        span = trace.span("llm_propose")
        proposal = propose_patch(req.instruction, STATE["indexer"], CONFIG, external_context=external_context)
        span.finish()
    except Exception as exc:
        raise HTTPException(400, f"propose failed: {exc}")
    STATE["pending_diff"] = proposal.diff
    STATE["pending_summary"] = proposal.summary
    STATE["pending_risk"] = proposal.risk_notes
    if STATE.get("state_store"):
        STATE["state_store"].write_pending_patch({
            "diff": proposal.diff,
            "summary": proposal.summary,
            "risk": proposal.risk_notes,
        })
    touched = _touched_files(proposal.diff)

    def gen():
        yield _sse_event("status", "started")
        if proposal.summary:
            yield _sse_event("summary", proposal.summary)
        if proposal.risk_notes:
            yield _sse_event("risk", proposal.risk_notes)
        for i in range(0, len(proposal.diff), 800):
            yield _sse_event("diff", proposal.diff[i:i + 800])
        yield _sse_event("meta", json.dumps({
            "touched_files": touched,
            "mcp": mcp_meta,
            "ingest": ingest_meta,
            "trace": trace.to_dict(),
        }))
        yield _sse_event("done", "")

    response = StreamingResponse(gen(), media_type="text/event-stream")
    response.headers["X-Trace-Id"] = trace.id
    trace.log("propose_stream")
    return response

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
    if STATE.get("state_store"):
        STATE["state_store"].write_pending_patch({
            "diff": proposal.diff,
            "summary": proposal.summary,
            "risk": proposal.risk_notes,
        })
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


@app.post("/revise_pending_stream")
def revise_pending_stream(req: ReviseRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if not STATE["pending_diff"]:
        raise HTTPException(400, "no pending diff")
    trace = TraceContext()
    external_context = _context_bundle_to_text(req.context)
    mcp_query = req.mcp_query
    if not mcp_query and _should_use_mcp(req.instruction):
        mcp_query = req.instruction
    span = trace.span("mcp")
    mcp_context, mcp_meta = _maybe_use_mcp(req.mcp_confirm, mcp_query)
    span.finish()
    external_context.extend(mcp_context)
    span = trace.span("context_ingest")
    external_context, ingest_meta = _maybe_ingest_context(req.instruction, external_context)
    span.finish()
    try:
        span = trace.span("llm_revise")
        proposal = revise_pending_patch(req.instruction, STATE["pending_diff"], STATE["indexer"], CONFIG, external_context=external_context)
        span.finish()
    except Exception as exc:
        raise HTTPException(400, f"revise failed: {exc}")
    STATE["pending_diff"] = proposal.diff
    STATE["pending_summary"] = proposal.summary
    STATE["pending_risk"] = proposal.risk_notes
    if STATE.get("state_store"):
        STATE["state_store"].write_pending_patch({
            "diff": proposal.diff,
            "summary": proposal.summary,
            "risk": proposal.risk_notes,
        })
    touched = _touched_files(proposal.diff)

    def gen():
        yield _sse_event("status", "started")
        if proposal.summary:
            yield _sse_event("summary", proposal.summary)
        if proposal.risk_notes:
            yield _sse_event("risk", proposal.risk_notes)
        for i in range(0, len(proposal.diff), 800):
            yield _sse_event("diff", proposal.diff[i:i + 800])
        yield _sse_event("meta", json.dumps({
            "touched_files": touched,
            "mcp": mcp_meta,
            "ingest": ingest_meta,
            "trace": trace.to_dict(),
        }))
        yield _sse_event("done", "")

    response = StreamingResponse(gen(), media_type="text/event-stream")
    response.headers["X-Trace-Id"] = trace.id
    trace.log("revise_pending_stream")
    return response

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
    return lines[:5] if lines else ["Review request", "Identify relevant files", "Propose minimal changes"]


def _maybe_use_mcp(confirm: str | None, query: str | None) -> tuple[list[str], dict]:
    meta = {"used": False, "server": None, "error": None}
    if not query:
        return [], meta
    # MCP use is always allowed (no YES gating)
    STATE["mcp_allowed"] = True
    save_state(Path(STATE["repo_root"]), {"mcp_allowed": True})
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


def _ensure_repo_map() -> None:
    if STATE.get("state_store") is None:
        return
    store: AgentStateStore = STATE["state_store"]
    branch = store.get_active_branch()
    repo_map_path = store.branch_root(branch) / "repo_map" / "repo_map.json"
    if not repo_map_path.exists():
        _build_repo_map(full=True)


def _build_repo_map(full: bool = False) -> None:
    repo_root = Path(STATE["repo_root"])
    dep_graph: DependencyGraph = STATE["dep_graph"]
    store: AgentStateStore = STATE["state_store"]
    repo_map_dir = store.branch_root(store.get_active_branch()) / "repo_map"
    cache_path = repo_map_dir / "cache.json"
    if full:
        for p in repo_root.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".js", ".ts", ".tsx"):
                dep_graph.update_file(p)
        cache = {}
    else:
        cache = {}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text())
            except Exception:
                cache = {}
        con = sqlite3.connect(STATE["indexer"].db_path)
        cur = con.cursor()
        rows = cur.execute("SELECT path, mtime FROM files").fetchall()
        con.close()
        for rel, mtime in rows:
            prev = cache.get(rel)
            if prev is None or float(prev) != float(mtime):
                p = repo_root / rel
                if p.exists():
                    dep_graph.update_file(p)
            cache[rel] = float(mtime)
    repo_map_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2))
    builder = RepoMapBuilder(repo_root=repo_root, index_db=STATE["indexer"].db_path, dep_db=dep_graph.db_path)
    builder.build(repo_map_dir)


def _handle_task(task: dict) -> dict:
    t = task.get("type")
    payload = task.get("payload") or {}
    if t == "REPO_MAP_REBUILD":
        _build_repo_map(full=bool(payload.get("full", False)))
        return {"ok": True}
    if t == "QUERY":
        req = QueryRequest(**payload)
        return query(req)
    if t == "PROPOSE":
        req = ProposeRequest(**payload)
        return propose(req)
    if t == "REVISE_PENDING":
        req = ReviseRequest(**payload)
        return revise_pending(req)
    return {"ok": False, "error": f"unknown task type: {t}"}

@app.post("/approve")
def _apply_approve(unified_diff: str, message: str | None) -> dict:
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if STATE["pending_diff"] is None:
        raise HTTPException(400, "no pending diff")
    if unified_diff != STATE["pending_diff"]:
        raise HTTPException(400, "approved diff does not match pending diff")
    try:
        STATE["staging"].check_unified_diff(unified_diff)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    try:
        STATE["staging"].apply_unified_diff_to_repo(unified_diff)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    message = message or "Approved change"
    # Re-index (MVP full re-index; later incremental)
    STATE["indexer"].index_all()
    # Clear pending context + reset RLM vars
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    if STATE.get("state_store"):
        STATE["state_store"].clear_pending_patch()
        try:
            STATE["state_store"].snapshot(STATE["snapshots"].get_head(), message="approved")
        except Exception:
            pass
    snap_meta = STATE["snapshots"].snapshot(message=message)
    minimal = build_minimal_meta(
        repo_root=Path(STATE["repo_root"]),
        head=STATE["snapshots"].get_head(),
        model_cfg={"reasoner": CONFIG.reasoner.__dict__, "coder": CONFIG.coder.__dict__, "vlm": CONFIG.vlm.__dict__},
        index_path=STATE["indexer"].db_path,
    )
    reset_context(Path(STATE["repo_root"]), minimal)
    return {
        "status":"ok",
        "snapshot": snap_meta.__dict__,
        "snapshots": STATE["snapshots"].list_snapshots(),
    }


@app.post("/approve")
def approve(req: ApproveRequest):
    return _apply_approve(req.unified_diff, req.message)


@app.post("/approve_stream")
def approve_stream(req: ApproveRequest):
    trace = TraceContext()

    def gen():
        try:
            yield _sse_event("status", "validating diff")
            if STATE["repo_root"] is None:
                yield _sse_event("error", "init first")
                return
            if STATE["pending_diff"] is None:
                yield _sse_event("error", "no pending diff")
                return
            if req.unified_diff != STATE["pending_diff"]:
                yield _sse_event("error", "approved diff does not match pending diff")
                return
            yield _sse_event("status", "checking diff")
            span = trace.span("check_diff")
            STATE["staging"].check_unified_diff(req.unified_diff)
            span.finish()
            yield _sse_event("status", "applying patch")
            span = trace.span("apply_patch")
            STATE["staging"].apply_unified_diff_to_repo(req.unified_diff)
            span.finish()
            yield _sse_event("status", "reindexing")
            span = trace.span("reindex")
            STATE["indexer"].index_all()
            span.finish()
            yield _sse_event("status", "snapshotting")
            span = trace.span("snapshot")
            message = req.message or "Approved change"
            if STATE.get("state_store"):
                STATE["state_store"].clear_pending_patch()
                try:
                    STATE["state_store"].snapshot(STATE["snapshots"].get_head(), message="approved")
                except Exception:
                    pass
            snap_meta = STATE["snapshots"].snapshot(message=message)
            span.finish()
            yield _sse_event("status", "resetting context")
            span = trace.span("reset_context")
            minimal = build_minimal_meta(
                repo_root=Path(STATE["repo_root"]),
                head=STATE["snapshots"].get_head(),
                model_cfg={"reasoner": CONFIG.reasoner.__dict__, "coder": CONFIG.coder.__dict__, "vlm": CONFIG.vlm.__dict__},
                index_path=STATE["indexer"].db_path,
            )
            reset_context(Path(STATE["repo_root"]), minimal)
            span.finish()
            STATE["pending_diff"] = None
            STATE["pending_summary"] = ""
            STATE["pending_risk"] = ""
            yield _sse_event("status", "done")
            yield _sse_event("meta", json.dumps({"snapshot": snap_meta.__dict__}))
            yield _sse_event("done", json.dumps(trace.to_dict()))
        except HTTPException as exc:
            yield _sse_event("error", str(exc.detail))
        except Exception as exc:
            yield _sse_event("error", str(exc))

    response = StreamingResponse(gen(), media_type="text/event-stream")
    response.headers["X-Trace-Id"] = trace.id
    trace.log("approve_stream")
    return response

@app.post("/reject")
def reject():
    if STATE["staging"] is None:
        raise HTTPException(400, "init first")
    STATE["staging"].reset()
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    if STATE.get("state_store"):
        STATE["state_store"].clear_pending_patch()
    return {"status":"ok"}

@app.get("/snapshots")
def snapshots_list():
    if STATE.get("snapshots") is None:
        raise HTTPException(400, "init first")
    return {"snapshots": STATE["snapshots"].list_snapshots()}

class SnapshotCreateRequest(BaseModel):
    message: str | None = None

@app.post("/snapshots/create")
def snapshots_create(req: SnapshotCreateRequest):
    if STATE.get("snapshots") is None:
        raise HTTPException(400, "init first")
    meta = STATE["snapshots"].snapshot(message=req.message or "manual")
    return {"status": "ok", "snapshot": meta.__dict__, "snapshots": STATE["snapshots"].list_snapshots()}

@app.post("/snapshots/restore")
def snapshots_restore(req: SnapshotRestoreRequest):
    if STATE.get("snapshots") is None:
        raise HTTPException(400, "init first")
    STATE["snapshots"].restore(req.snapshot_id)
    STATE["indexer"].index_all()
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    return {"status": "ok", "head": STATE["snapshots"].get_head()}

# Compatibility endpoints for legacy clients
@app.post("/restore_remote")
def restore_remote(req: RestoreRemoteRequest):
    return {
        "status": "ok",
        "restore_remote_url": "",
        "disabled": True,
        "message": "git restore disabled; snapshots are local-only",
    }

@app.get("/restore_points")
def restore_points():
    if STATE.get("snapshots") is None:
        raise HTTPException(400, "init first")
    return {"restore_points": [s.get("snapshot_id") for s in STATE["snapshots"].list_snapshots()]}

class RevertRequest(BaseModel):
    sha: str

@app.post("/revert")
def revert(req: RevertRequest):
    if STATE.get("snapshots") is None:
        raise HTTPException(400, "init first")
    try:
        STATE["snapshots"].restore(req.sha)
    except Exception as exc:
        raise HTTPException(400, f"snapshot not found: {exc}")
    STATE["indexer"].index_all()
    STATE["pending_diff"] = None
    STATE["pending_summary"] = ""
    STATE["pending_risk"] = ""
    return {"status": "ok", "head": STATE["snapshots"].get_head()}

@app.post("/reset_context")
def reset_context_endpoint():
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    head = STATE["snapshots"].get_head()
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

@app.post("/session/start")
def session_start(req: SessionStartRequest):
    repo = Path(req.repo_root).resolve()
    sid = f"session_{int(time.time())}"
    store = AgentStateStore(repo_root=repo, session_id=sid)
    store.ensure_session("main")
    STATE["session_id"] = sid
    STATE["state_store"] = store
    return {"session_id": sid, "active_branch": store.get_active_branch()}

@app.get("/session/status")
def session_status():
    if STATE.get("state_store") is None:
        raise HTTPException(400, "no session")
    store: AgentStateStore = STATE["state_store"]
    return {
        "session_id": STATE.get("session_id"),
        "active_branch": store.get_active_branch(),
        "branches": store.list_branches(),
    }

@app.post("/branch/create")
def branch_create(req: BranchCreateRequest):
    if STATE.get("state_store") is None:
        raise HTTPException(400, "no session")
    store: AgentStateStore = STATE["state_store"]
    store.ensure_session(req.name)
    return {"status": "ok", "branch": req.name}

@app.post("/branch/switch")
def branch_switch(req: BranchSwitchRequest):
    if STATE.get("state_store") is None:
        raise HTTPException(400, "no session")
    store: AgentStateStore = STATE["state_store"]
    store.switch_branch(req.name)
    pending = store.read_pending_patch()
    STATE["pending_diff"] = pending.get("diff")
    STATE["pending_summary"] = pending.get("summary", "")
    STATE["pending_risk"] = pending.get("risk", "")
    return {"status": "ok", "active_branch": store.get_active_branch()}

@app.post("/agent_state/snapshot")
def agent_state_snapshot(req: SnapshotRequest):
    if STATE.get("state_store") is None:
        raise HTTPException(400, "no session")
    store: AgentStateStore = STATE["state_store"]
    snap = store.snapshot(STATE["snapshots"].get_head(), message=req.message or "")
    return {"snapshot_id": snap}

@app.post("/agent_state/restore")
def agent_state_restore(req: SnapshotRestoreRequest):
    if STATE.get("state_store") is None:
        raise HTTPException(400, "no session")
    store: AgentStateStore = STATE["state_store"]
    store.restore_snapshot(req.snapshot_id)
    pending = store.read_pending_patch()
    STATE["pending_diff"] = pending.get("diff")
    STATE["pending_summary"] = pending.get("summary", "")
    STATE["pending_risk"] = pending.get("risk", "")
    return {"status": "ok"}

@app.get("/repo_map")
def repo_map():
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    store: AgentStateStore = STATE["state_store"]
    branch = store.get_active_branch()
    repo_map_path = store.branch_root(branch) / "repo_map" / "repo_map.json"
    if not repo_map_path.exists():
        _build_repo_map(full=True)
    return json.loads(repo_map_path.read_text())

@app.post("/repo_map/rebuild")
def repo_map_rebuild(req: RepoMapRebuildRequest):
    _build_repo_map(full=req.full)
    return {"status": "ok"}

@app.post("/task/submit")
def task_submit(req: TaskSubmitRequest):
    if STATE.get("task_queue") is None:
        raise HTTPException(400, "init first")
    tid = STATE["task_queue"].submit(req.type, req.payload)
    return {"task_id": tid}

@app.post("/task/status")
def task_status(req: TaskStatusRequest):
    if STATE.get("task_queue") is None:
        raise HTTPException(400, "init first")
    return STATE["task_queue"].status(req.task_id)

@app.get("/task/list")
def task_list(limit: int = 50):
    if STATE.get("task_queue") is None:
        raise HTTPException(400, "init first")
    return {"tasks": STATE["task_queue"].list(limit=limit)}

@app.post("/task/cancel")
def task_cancel(req: TaskCancelRequest):
    if STATE.get("task_queue") is None:
        raise HTTPException(400, "init first")
    STATE["task_queue"].cancel(req.task_id)
    return {"status": "ok"}

@app.post("/task/logs")
def task_logs(req: TaskLogsRequest):
    if STATE.get("task_queue") is None:
        raise HTTPException(400, "init first")
    return {"logs": STATE["task_queue"].read_logs(req.task_id, req.after)}

@app.get("/worker/status")
def worker_status():
    w = STATE.get("task_worker")
    if w is None:
        return {"running": False}
    return {
        "running": w.is_alive(),
        "last_tick": getattr(w, "last_tick", None),
        "processed": getattr(w, "processed", 0),
        "current_task": getattr(w, "current_task", ""),
        "last_error": getattr(w, "last_error", ""),
        "started_at": getattr(w, "started_at", None),
        "thread_id": getattr(w, "ident", None),
        "queue_size": len(STATE["task_queue"].list(limit=1000)) if STATE.get("task_queue") else 0,
    }

@app.get("/models")
def get_models():
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    repo_root = Path(STATE["repo_root"])
    return {
        "reasoner": list_models("reasoner", CONFIG, repo_root, CONFIG_PATH),
        "coder": list_models("coder", CONFIG, repo_root, CONFIG_PATH),
        "vlm": list_models("vlm", CONFIG, repo_root, CONFIG_PATH),
    }

@app.post("/models/select")
def select_model(req: ModelSelectRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.role not in ("reasoner", "coder", "vlm"):
        raise HTTPException(400, "role must be reasoner, coder, or vlm")
    set_selected(req.role, req.model_id, Path(STATE["repo_root"]), CONFIG_PATH)
    return {"status": "ok"}

@app.post("/models/add")
def add_model(req: ModelAddRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.role not in ("reasoner", "coder", "vlm"):
        raise HTTPException(400, "role must be reasoner, coder, or vlm")
    # Validate repo has GGUFs
    try:
        from huggingface_hub import list_repo_files  # type: ignore
        files = list_repo_files(req.repo_id)
        ggufs = [f for f in files if f.lower().endswith(".gguf")]
        if not ggufs:
            raise HTTPException(400, "repo contains no .gguf files")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"unable to validate repo: {exc}")
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    cfg.setdefault("model_registry", {})
    cfg["model_registry"].setdefault(req.role, {})
    cfg["model_registry"][req.role].setdefault("options", [])
    options = cfg["model_registry"][req.role]["options"]
    if any(o.get("id") == req.model_id for o in options):
        raise HTTPException(400, "model_id already exists")
    model_dir = req.model_dir or req.model_id
    opt = {
        "id": req.model_id,
        "provider": "local",
        "role": req.role,
        "model_dir": model_dir,
        "repo_id": req.repo_id,
        "filename_hint": req.filename_hint or "Q4_K_M",
        "context": req.context or 8192,
    }
    options.append(opt)
    if req.role == "vlm":
        cfg.setdefault("models", {})
        cfg["models"].setdefault("vlm", {})
        cfg["models"]["vlm"]["enabled"] = True
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    if req.download_now:
        try:
            from scripts.download_models import download_one  # type: ignore
            hint = req.filename_hint or "Q4_K_M"
            out_dir = Path(APP_ROOT / "models" / model_dir)
            download_one(req.repo_id, hint, out_dir)
        except Exception as exc:
            raise HTTPException(400, f"download failed: {exc}")
    return {"status": "ok"}

@app.post("/models/remove")
def remove_model(req: ModelRemoveRequest):
    if STATE["repo_root"] is None:
        raise HTTPException(400, "init first")
    if req.role not in ("reasoner", "coder", "vlm"):
        raise HTTPException(400, "role must be reasoner, coder, or vlm")
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    options = (cfg.get("model_registry") or {}).get(req.role, {}).get("options", [])
    remaining = [o for o in options if o.get("id") not in set(req.model_ids)]
    removed = [o for o in options if o.get("id") in set(req.model_ids)]
    if cfg.get("model_registry") and cfg["model_registry"].get(req.role):
        cfg["model_registry"][req.role]["options"] = remaining
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    # Remove model files if downloaded
    for opt in removed:
        model_dir = opt.get("model_dir") or opt.get("id")
        if not model_dir:
            continue
        path = Path(APP_ROOT / "models" / model_dir)
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    return {"status": "ok", "removed": [o.get(\"id\") for o in removed]}

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
    STATE["mcp_allowed"] = True
    if STATE["repo_root"] is not None:
        save_state(Path(STATE["repo_root"]), {"mcp_allowed": True})
    return {"status": "ok", "mcp_allowed": True}

@app.post("/mcp/revoke")
def mcp_revoke(req: MCPRevokeRequest):
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
