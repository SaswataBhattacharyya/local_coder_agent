from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from typing import List, Dict, Tuple

from rlm_wrap.runtime import RLMChatRuntime
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


def propose_patch(user_text: str, indexer: SymbolIndexer, config: AppConfig, external_context: List[str] | None = None) -> Proposal:
    context_blocks = _build_context(user_text, indexer)
    plan = [
        "Locate relevant files and symbols",
        "Apply the requested change carefully",
        "Return a minimal unified diff",
    ]
    prompt = _build_prompt(user_text, plan, context_blocks, external_context or [])
    model_dir = Path(config.paths.models_dir) / "coder"
    var_store = RLMVarStore(repo_root=indexer.repo_root)
    runtime = RLMChatRuntime(
        model_dir=model_dir,
        filename_hint=config.coder.filename_hint,
        n_ctx=config.coder.context,
        var_store=var_store,
    )
    raw = runtime.chat([
        {"role": "system", "content": "You are a coding assistant. Output a unified diff only, plus a one-line SUMMARY and RISK line."},
        {"role": "user", "content": prompt},
    ], vars={"context_blocks": context_blocks, "plan": plan})
    summary = _extract_line(raw, "SUMMARY:")
    risk = _extract_line(raw, "RISK:")
    diff = _extract_diff(raw)
    if not diff:
        raise RuntimeError("Model response did not include a unified diff.")
    return Proposal(diff=diff, summary=summary, risk_notes=risk)


def revise_pending_patch(user_text: str, pending_diff: str, indexer: SymbolIndexer, config: AppConfig, external_context: List[str] | None = None) -> Proposal:
    context_blocks = _build_context(user_text, indexer)
    prompt = _build_revise_prompt(user_text, pending_diff, context_blocks, external_context or [])
    model_dir = Path(config.paths.models_dir) / "coder"
    var_store = RLMVarStore(repo_root=indexer.repo_root)
    runtime = RLMChatRuntime(
        model_dir=model_dir,
        filename_hint=config.coder.filename_hint,
        n_ctx=config.coder.context,
        var_store=var_store,
    )
    raw = runtime.chat([
        {"role": "system", "content": "You are a coding assistant. Update the diff minimally. Output a unified diff only, plus a one-line SUMMARY and RISK line."},
        {"role": "user", "content": prompt},
    ], vars={"context_blocks": context_blocks})
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
