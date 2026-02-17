from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class AgentState(str, Enum):
    IDLE = "IDLE"
    NEEDS_INFO = "NEEDS_INFO"
    READY = "READY"


@dataclass
class AgentSession:
    state: AgentState = AgentState.IDLE
    questions: List[str] = field(default_factory=list)

    def set_needs_info(self, questions: List[str]) -> None:
        self.state = AgentState.NEEDS_INFO
        self.questions = questions

    def set_ready(self) -> None:
        self.state = AgentState.READY
        self.questions = []
