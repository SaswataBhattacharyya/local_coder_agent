from __future__ import annotations
from dataclasses import dataclass
import re


INFO_PATTERNS = [
    r"\bsummarize\b",
    r"\bsummarise\b",
    r"\bsummarising\b",
    r"\bsummarizing\b",
    r"\bsummary\b",
    r"\bwhat is this\b",
    r"\bwhat's this\b",
    r"\boverview\b",
    r"\barchitecture\b",
    r"\bexplain\b",
    r"\bhow to run\b",
    r"\bhow do i run\b",
    r"\bhow to start\b",
    r"\bhow do i start\b",
    r"\bhow it starts\b",
    r"\bhow it start\b",
    r"\bhow to build\b",
    r"\bhow to test\b",
    r"\bsetup\b",
    r"\binstall\b",
    r"\busage\b",
]

COMMAND_PATTERNS = [
    r"\brun tests\b",
    r"\brun build\b",
    r"\brun lint\b",
    r"\brun\b",
    r"\bexecute\b",
    r"\bstart server\b",
    r"\bnpm\b",
    r"\bpytest\b",
    r"\bmake\b",
]

EDIT_PATTERNS = [
    r"\bfix\b",
    r"\bchange\b",
    r"\bupdate\b",
    r"\badd\b",
    r"\bremove\b",
    r"\brefactor\b",
    r"\bimplement\b",
    r"\bbug\b",
    r"\bissue\b",
    r"\bfeature\b",
]

MCP_PATTERNS = [
    r"\bbrowse\b",
    r"\bsearch\b",
    r"\bgoogle\b",
    r"\bwebsite\b",
    r"\burl\b",
    r"\bhttp[s]?://",
]

_PATH_HINT = re.compile(r"[/\\]|\.py\b|\.js\b|\.ts\b|\.tsx\b|\.json\b|\.yml\b|\.yaml\b|\.md\b|\.html\b|\.css\b")
_PRONOUN_HINT = re.compile(r"\b(it|this|that|these|those)\b", re.IGNORECASE)


@dataclass
class IntentContext:
    repo_root_known: bool
    has_pending_patch: bool


class IntentRouter:
    def classify(self, user_text: str, ctx: IntentContext) -> str:
        text = user_text.strip().lower()
        if not text:
            return "AMBIGUOUS"

        if any(re.search(p, text) for p in INFO_PATTERNS):
            return "INFO"

        if any(re.search(p, text) for p in MCP_PATTERNS):
            return "MCP"

        if any(re.search(p, text) for p in COMMAND_PATTERNS):
            # "how to run" should be INFO not COMMAND
            if "how to" in text or "how do i" in text:
                return "INFO"
            return "COMMAND"

        if any(re.search(p, text) for p in EDIT_PATTERNS):
            return "EDIT"

        # Short or pronoun-only -> ambiguous
        words = text.split()
        if len(words) < 4 and not _PATH_HINT.search(text):
            return "AMBIGUOUS"
        if _PRONOUN_HINT.search(text) and not _PATH_HINT.search(text):
            return "AMBIGUOUS"

        return "EDIT"
