from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple
import re

from rlm_wrap.store import RLMVarStore

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
STOP = {
    "the", "and", "for", "with", "from", "that", "this", "then", "else", "when", "where",
    "into", "also", "make", "update", "change", "modify", "remove", "add", "code", "file",
}


@dataclass
class IngestResult:
    summary: str
    top_chunks: List[str]
    chunks: List[str]


def ingest_text(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(0, end - chunk_overlap)
    return chunks


def summarize_chunks(chunks: List[str]) -> str:
    # Simple heuristic summary: keyword list + chunk count
    words: Dict[str, int] = {}
    for ch in chunks:
        for w in WORD_RE.findall(ch.lower()):
            if w in STOP:
                continue
            words[w] = words.get(w, 0) + 1
    top = sorted(words.items(), key=lambda x: (-x[1], x[0]))[:12]
    kws = ", ".join([w for w, _ in top])
    return f"Context ingested: {len(chunks)} chunks. Keywords: {kws}"


def rank_chunks(query: str, chunks: List[str], top_k: int) -> List[str]:
    qwords = set([w for w in WORD_RE.findall(query.lower()) if w not in STOP])
    if not qwords:
        return chunks[:top_k]
    scored = []
    for ch in chunks:
        cwords = set([w for w in WORD_RE.findall(ch.lower()) if w not in STOP])
        score = len(qwords.intersection(cwords))
        scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for s, c in scored[:top_k]]


def ingest_and_store(text: str, query: str, store: RLMVarStore, chunk_size: int, chunk_overlap: int, top_k: int) -> IngestResult:
    chunks = ingest_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    store.set("context_chunks", chunks)
    summary = summarize_chunks(chunks)
    top = rank_chunks(query, chunks, top_k=top_k)
    return IngestResult(summary=summary, top_chunks=top, chunks=chunks)
