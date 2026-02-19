from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import re

from agent.intent_router import _PATH_HINT, _PRONOUN_HINT


@dataclass
class PlannerInput:
    user_text: str
    intent: str
    repo_root_known: bool
    has_pending_patch: bool


@dataclass
class PlannerOutput:
    state: str
    questions: List[str]
    plan: List[str]
    use_mcp: bool
    mcp_server: Optional[str]
    intent: str
    needs_confirm: bool = False
    confirm_token: str | None = None


class PlannerFSM:
    def handle(self, inp: PlannerInput) -> PlannerOutput:
        text = inp.user_text.strip()
        if inp.intent == "INFO":
            if not inp.repo_root_known:
                return PlannerOutput(
                    state="NEEDS_INFO",
                    questions=["Please call /init with repo_root or provide the repo path."],
                    plan=[],
                    use_mcp=False,
                    mcp_server=None,
                    intent=inp.intent,
                )
            plan = [
                "Read README/docs for usage",
                "Inspect package.json/pyproject/Makefile for scripts",
                "Use repo map/index to summarize structure",
                "Summarize how to start/run the project",
            ]
            return PlannerOutput(
                state="READY",
                questions=[],
                plan=plan,
                use_mcp=False,
                mcp_server=None,
                intent=inp.intent,
            )

        if inp.intent == "MCP":
            plan = [
                "Use MCP tools to gather external context",
                "Summarize findings for the user",
            ]
            return PlannerOutput(
                state="READY",
                questions=[],
                plan=plan,
                use_mcp=True,
                mcp_server="playwright",
                intent=inp.intent,
            )

        if inp.intent == "COMMAND":
            return PlannerOutput(
                state="NEEDS_INFO",
                questions=["Commands require explicit confirmation. Provide the exact command to run."],
                plan=[],
                use_mcp=False,
                mcp_server=None,
                intent=inp.intent,
                needs_confirm=True,
                confirm_token="YES",
            )

        if inp.intent == "EDIT":
            if inp.has_pending_patch and _looks_like_revision(text):
                plan = [
                    "Revise pending patch based on new instruction",
                    "Update diff and summary",
                ]
                return PlannerOutput(
                    state="READY",
                    questions=[],
                    plan=plan,
                    use_mcp=False,
                    mcp_server=None,
                    intent=inp.intent,
                )
            if _needs_scope(text):
                return PlannerOutput(
                    state="NEEDS_INFO",
                    questions=["Which file or area should I change?"],
                    plan=[],
                    use_mcp=False,
                    mcp_server=None,
                    intent=inp.intent,
                )
            plan = [
                "Locate relevant files and symbols",
                "Identify necessary changes",
                "Prepare a patch proposal",
            ]
            return PlannerOutput(
                state="READY",
                questions=[],
                plan=plan,
                use_mcp=False,
                mcp_server=None,
                intent=inp.intent,
            )

        # AMBIGUOUS
        return PlannerOutput(
            state="NEEDS_INFO",
            questions=["Is this an explanation request or a code change?"],
            plan=[],
            use_mcp=False,
            mcp_server=None,
            intent=inp.intent,
        )


def _needs_scope(text: str) -> bool:
    if not text:
        return True
    has_path = bool(_PATH_HINT.search(text))
    has_pronoun = bool(_PRONOUN_HINT.search(text))
    words = text.split()
    if len(words) < 4 and not has_path:
        return True
    if has_pronoun and not has_path:
        return True
    return False


def _looks_like_revision(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ["change more", "revise", "update", "tweak", "modify", "adjust"])
