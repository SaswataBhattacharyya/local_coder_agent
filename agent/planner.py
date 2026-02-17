from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List, Optional

from agent.state import AgentSession


_PATH_HINT = re.compile(r"[/\\]|\.py\b|\.js\b|\.ts\b|\.tsx\b|\.json\b|\.yml\b|\.yaml\b|\.md\b|\.html\b|\.css\b")
_PRONOUN_HINT = re.compile(r"\b(it|this|that|these|those)\b", re.IGNORECASE)


@dataclass
class QueryResult:
    state: str
    questions: List[str]
    plan: List[str]
    use_mcp: bool
    mcp_server: Optional[str]


class QueryPlanner:
    def __init__(self, session: AgentSession) -> None:
        self.session = session

    def analyze(self, user_text: str) -> QueryResult:
        text = user_text.strip()
        wants_browser = self._wants_browser(text)
        if not text:
            questions = ["What would you like to change, and in which files or areas?"]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=[], use_mcp=False, mcp_server=None)

        words = text.split()
        has_path = bool(_PATH_HINT.search(text))
        has_pronoun = bool(_PRONOUN_HINT.search(text))

        if len(words) < 4 and not has_path:
            questions = [
                "Which files or components should I focus on?",
                "What is the desired outcome or behavior?",
            ]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=[], use_mcp=False, mcp_server=None)

        if has_pronoun and not has_path:
            questions = [
                "Which file or module are you referring to?",
                "What change do you want to see?",
            ]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=[], use_mcp=False, mcp_server=None)

        self.session.set_ready()
        plan = [
            "Locate relevant files and symbols",
            "Identify necessary changes",
            "Prepare a patch proposal",
        ]
        if wants_browser:
            plan.insert(0, "Use MCP browser tools to gather external context (requires YES confirmation)")
            questions = ["I can use the Playwright MCP browser. Reply YES once to allow tool use (or call /mcp/allow), or NO to proceed without browsing."]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=plan, use_mcp=True, mcp_server="playwright")
        return QueryResult(state=self.session.state.value, questions=[], plan=plan, use_mcp=False, mcp_server=None)

    def _wants_browser(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in ["browse", "browser", "web", "search", "google", "brave", "playwright"])
