from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional

from agent.state import AgentSession
from agent.intent_router import IntentRouter, IntentContext
from agent.planner_fsm import PlannerFSM, PlannerInput


@dataclass
class QueryResult:
    state: str
    questions: List[str]
    plan: List[str]
    use_mcp: bool
    mcp_server: Optional[str]
    intent: Optional[str] = None
    needs_confirm: bool = False
    confirm_token: Optional[str] = None


class QueryPlanner:
    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.router = IntentRouter()
        self.fsm = PlannerFSM()

    def analyze(self, user_text: str, repo_root_known: bool = False, has_pending_patch: bool = False) -> QueryResult:
        ctx = IntentContext(repo_root_known=repo_root_known, has_pending_patch=has_pending_patch)
        intent = self.router.classify(user_text, ctx)
        out = self.fsm.handle(
            PlannerInput(
                user_text=user_text,
                intent=intent,
                repo_root_known=repo_root_known,
                has_pending_patch=has_pending_patch,
            )
        )
        if out.state == "NEEDS_INFO":
            self.session.set_needs_info(out.questions)
        else:
            self.session.set_ready()
        return QueryResult(
            state=self.session.state.value,
            questions=out.questions,
            plan=out.plan,
            use_mcp=out.use_mcp,
            mcp_server=out.mcp_server,
            intent=out.intent,
            needs_confirm=out.needs_confirm,
            confirm_token=out.confirm_token,
        )
