from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List

from agent.state import AgentSession


_PATH_HINT = re.compile(r"[/\\]|\.py\b|\.js\b|\.ts\b|\.tsx\b|\.json\b|\.yml\b|\.yaml\b|\.md\b|\.html\b|\.css\b")
_PRONOUN_HINT = re.compile(r"\b(it|this|that|these|those)\b", re.IGNORECASE)


@dataclass
class QueryResult:
    state: str
    questions: List[str]
    plan: List[str]


class QueryPlanner:
    def __init__(self, session: AgentSession) -> None:
        self.session = session

    def analyze(self, user_text: str) -> QueryResult:
        text = user_text.strip()
        if not text:
            questions = ["What would you like to change, and in which files or areas?"]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=[])

        words = text.split()
        has_path = bool(_PATH_HINT.search(text))
        has_pronoun = bool(_PRONOUN_HINT.search(text))

        if len(words) < 4 and not has_path:
            questions = [
                "Which files or components should I focus on?",
                "What is the desired outcome or behavior?",
            ]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=[])

        if has_pronoun and not has_path:
            questions = [
                "Which file or module are you referring to?",
                "What change do you want to see?",
            ]
            self.session.set_needs_info(questions)
            return QueryResult(state=self.session.state.value, questions=questions, plan=[])

        self.session.set_ready()
        plan = [
            "Locate relevant files and symbols",
            "Identify necessary changes",
            "Prepare a patch proposal",
        ]
        return QueryResult(state=self.session.state.value, questions=[], plan=plan)
