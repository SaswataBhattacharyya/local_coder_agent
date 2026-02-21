from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import re
import sqlite3
from typing import List, Dict, Tuple, Any

from agent.llm_router import chat as llm_chat
from rlm_wrap.store import RLMVarStore
from indexer.indexer import SymbolIndexer
from agent.config import AppConfig

_PATH_RE = re.compile(r"[\w./-]+\.(?:py|js|ts|tsx|json|yml|yaml|md|html|css)\b")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

_STOPWORDS = {
    "this", "that", "with", "from", "then", "else", "when", "where", "what", "which",
    "into", "also", "make", "update", "change", "modify", "remove", "add", "code",
    "file", "files", "function", "class", "method", "server", "client", "agent",
}


@dataclass
class Proposal:
    diff: str
    summary: str
    risk_notes: str




# --- Multi-step edit pipeline ---

def propose_patch_multistep(user_text: str, indexer: SymbolIndexer, config: AppConfig, external_context: List[str] | None = None) -> Proposal:
    _store_rlm_context(indexer)
    plan = _plan_edit_steps(user_text, indexer, config)
    files = plan.get("files", [])[: getattr(config.runtime, "multi_step_max_files", 5)]
    steps = plan.get("steps", [])
    checks = plan.get("checks", [])

    diffs: List[str] = []
    summaries: List[str] = []
    risks: List[str] = []

    for f in files:
        file_path = f.get("path") if isinstance(f, dict) else str(f)
        if not file_path:
            continue
        snippet = indexer.get_file_head(file_path, max_lines=300)
        context_blocks = [{"file": file_path, "snippet": snippet}] if snippet else []
        prompt = _build_prompt_for_file(user_text, steps, context_blocks, external_context or [], file_path)
        raw = llm_chat("coder", [
            {"role": "system", "content": "You are a coding assistant. Return a unified diff only, plus a one-line SUMMARY and RISK line."},
            {"role": "user", "content": prompt},
        ], config, indexer.repo_root, config_path=Path(__file__).resolve().parents[1] / "configs" / "config.yaml")
        summaries.append(_extract_line(raw, "SUMMARY:"))
        risks.append(_extract_line(raw, "RISK:"))
        diff = _extract_diff(raw)
        if diff:
            diffs.append(diff)

    if not diffs:
        raise RuntimeError("Model response did not include a unified diff.")
    combined = "\n\n".join(diffs)
    summary = "; ".join([s for s in summaries if s])
    risk = "; ".join([r for r in risks if r])
    if checks:
        risk = (risk + "\nSuggested checks: " + ", ".join(checks)).strip()
    return Proposal(diff=combined, summary=summary, risk_notes=risk)


def _plan_edit_steps(user_text: str, indexer: SymbolIndexer, config: AppConfig) -> Dict[str, Any]:
    top_files = _get_top_files(indexer, limit=20)
    prompt = (
        "You are planning a code change. Return strict JSON with keys: files, steps, checks. "
        "files is a list of objects {path, reason}. steps is a list of short strings. "
        "checks is a list of commands to run. Use files from the repository list.\n\n"
        f"User request:\n{user_text}\n\nRepo files (top):\n" + "\n".join(top_files)
    )
    raw = llm_chat("reasoner", [
        {"role": "system", "content": "You are a software planning assistant."},
        {"role": "user", "content": prompt},
    ], config, indexer.repo_root, config_path=Path(__file__).resolve().parents[1] / "configs" / "config.yaml")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"files": [], "steps": ["Review request"], "checks": []}


def _build_prompt_for_file(user_text: str, steps: List[str], context_blocks: List[Dict[str, str]], external_context: List[str], file_path: str) -> str:
    parts = []
    parts.append("Task:")
    parts.append(user_text.strip())
    parts.append("")
    parts.append("Plan:")
    for step in steps:
        parts.append(f"- {step}")
    parts.append("")
    parts.append(f"File to edit: {file_path}")
    parts.append("Only modify this file.")
    parts.append("")
    if context_blocks:
        parts.append("Context:")
        for blk in context_blocks:
            parts.append(f"File: {blk['file']}")
            parts.append(blk["snippet"])
            parts.append("")
    if external_context:
        parts.append("External Context:")
        for item in external_context:
            parts.append(item)
            parts.append("")
    parts.append("Return a unified diff against the repo root. Avoid unrelated changes.")
    return "\n".join(parts)


def _get_top_files(indexer: SymbolIndexer, limit: int = 50) -> List[str]:
    indexer.init_db()
    con = sqlite3.connect(indexer.db_path)
    cur = con.cursor()
    rows = cur.execute("SELECT path FROM files LIMIT ?", (limit,)).fetchall()
    con.close()
    return [r[0] for r in rows]


def _store_rlm_context(indexer: SymbolIndexer) -> None:
    try:
        store = RLMVarStore(repo_root=indexer.repo_root)
        top_files = _get_top_files(indexer, limit=200)
        store.set_many({"repo_files": top_files})
    except Exception:
        pass


def propose_patch(user_text: str, indexer: SymbolIndexer, config: AppConfig, external_context: List[str] | None = None) -> Proposal:
    if getattr(config.runtime, "multi_step_edits", False):
        return propose_patch_multistep(user_text, indexer, config, external_context=external_context)
    context_blocks = _build_context(user_text, indexer)
    plan = [
        "Locate relevant files and symbols",
        "Apply the requested change carefully",
        "Return a minimal unified diff",
    ]
    prompt = _build_prompt(user_text, plan, context_blocks, external_context or [])
    raw = llm_chat("coder", [
        {"role": "system", "content": "You are a coding assistant. Output a unified diff only, plus a one-line SUMMARY and RISK line."},
        {"role": "user", "content": prompt},
    ], config, indexer.repo_root, config_path=Path(__file__).resolve().parents[1] / "configs" / "config.yaml")
    summary = _extract_line(raw, "SUMMARY:")
    risk = _extract_line(raw, "RISK:")
    diff = _extract_diff(raw)
    if not diff:
        raise RuntimeError("Model response did not include a unified diff.")
    return Proposal(diff=diff, summary=summary, risk_notes=risk)


def revise_pending_patch(user_text: str, pending_diff: str, indexer: SymbolIndexer, config: AppConfig, external_context: List[str] | None = None) -> Proposal:
    context_blocks = _build_context(user_text, indexer)
    prompt = _build_revise_prompt(user_text, pending_diff, context_blocks, external_context or [])
    raw = llm_chat("coder", [
        {"role": "system", "content": "You are a coding assistant. Update the diff minimally. Output a unified diff only, plus a one-line SUMMARY and RISK line."},
        {"role": "user", "content": prompt},
    ], config, indexer.repo_root, config_path=Path(__file__).resolve().parents[1] / "configs" / "config.yaml")
    summary = _extract_line(raw, "SUMMARY:")
    risk = _extract_line(raw, "RISK:")
    diff = _extract_diff(raw)
    if not diff:
        raise RuntimeError("Model response did not include a unified diff.")
    return Proposal(diff=diff, summary=summary, risk_notes=risk)


def _build_context(user_text: str, indexer: SymbolIndexer) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    seen_files = set()

    for match in _PATH_RE.finditer(user_text):
        raw_path = match.group(0)
        p = Path(raw_path)
        if p.is_absolute():
            try:
                file_rel = str(p.relative_to(indexer.repo_root))
            except ValueError:
                continue
        else:
            file_rel = raw_path
        if (indexer.repo_root / file_rel).exists() and file_rel not in seen_files:
            snippet = indexer.get_file_head(file_rel, max_lines=200)
            if snippet:
                blocks.append({"file": file_rel, "snippet": snippet})
                seen_files.add(file_rel)

    keywords = _extract_keywords(user_text)
    for kw in keywords[:3]:
        hits = indexer.rg_search(kw, max_results=20)
        for hit in hits:
            file_rel = hit["file"]
            if file_rel in seen_files and len(blocks) >= 4:
                continue
            snippet = indexer.get_snippet(file_rel, hit["line"], window=6)
            if snippet:
                blocks.append({"file": file_rel, "snippet": snippet})
                seen_files.add(file_rel)
            if len(blocks) >= 4:
                break
        if len(blocks) >= 4:
            break

    return blocks


def _extract_keywords(text: str) -> List[str]:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    filtered = [w for w in words if w not in _STOPWORDS]
    # Prefer longer tokens first
    filtered.sort(key=len, reverse=True)
    seen = set()
    out = []
    for w in filtered:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _build_prompt(user_text: str, plan: List[str], context_blocks: List[Dict[str, str]], external_context: List[str]) -> str:
    parts = []
    parts.append("Task:")
    parts.append(user_text.strip())
    parts.append("")
    parts.append("Plan:")
    for step in plan:
        parts.append(f"- {step}")
    parts.append("")
    if context_blocks:
        parts.append("Context:")
        for blk in context_blocks:
            parts.append(f"File: {blk['file']}")
            parts.append(blk["snippet"])
            parts.append("")
    if external_context:
        parts.append("External Context:")
        for item in external_context:
            parts.append(item)
            parts.append("")
    parts.append("Return a unified diff against the repo root. Avoid unrelated changes.")
    return "\n".join(parts)


def _build_revise_prompt(user_text: str, pending_diff: str, context_blocks: List[Dict[str, str]], external_context: List[str]) -> str:
    parts = []
    parts.append("We have an existing pending unified diff. Revise it minimally based on the new instruction.")
    parts.append("")
    parts.append("Instruction:")
    parts.append(user_text.strip())
    parts.append("")
    parts.append("Pending Diff:")
    parts.append(pending_diff.strip())
    parts.append("")
    if context_blocks:
        parts.append("Context:")
        for blk in context_blocks:
            parts.append(f"File: {blk['file']}")
            parts.append(blk["snippet"])
            parts.append("")
    if external_context:
        parts.append("External Context:")
        for item in external_context:
            parts.append(item)
            parts.append("")
    parts.append("Return an updated unified diff against the repo root. Avoid unrelated changes.")
    return "\n".join(parts)


def _extract_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.split(prefix, 1)[1].strip()
    return ""


def _extract_diff(text: str) -> str:
    if "```diff" in text:
        start = text.find("```diff")
        end = text.find("```", start + 6)
        if end != -1:
            return text[start + 7:end].strip()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("diff --git"):
            return "\n".join(lines[i:]).strip()
    return ""
