from agent.planner import QueryPlanner
from agent.state import AgentSession


def test_info_request_ready():
    planner = QueryPlanner(AgentSession())
    res = planner.analyze("Summarize this project and how to run it", repo_root_known=True)
    assert res.state == "READY"
    assert res.questions == []
    assert res.intent == "INFO"

def test_info_request_summarise_variant():
    planner = QueryPlanner(AgentSession())
    res = planner.analyze("Summarise this project and how it starts", repo_root_known=True)
    assert res.state == "READY"
    assert res.questions == []
    assert res.intent == "INFO"


def test_info_request_needs_repo():
    planner = QueryPlanner(AgentSession())
    res = planner.analyze("Summarize this project", repo_root_known=False)
    assert res.state == "NEEDS_INFO"
    assert res.questions


def test_mcp_request_no_gating():
    planner = QueryPlanner(AgentSession())
    res = planner.analyze("Browse the website for details", repo_root_known=True)
    assert res.state == "READY"
    assert res.use_mcp is True
    assert res.questions == []


def test_edit_request_needs_scope_once():
    planner = QueryPlanner(AgentSession())
    res = planner.analyze("Change it", repo_root_known=True)
    assert res.state == "NEEDS_INFO"
    assert len(res.questions) == 1


def test_command_request_needs_confirm():
    planner = QueryPlanner(AgentSession())
    res = planner.analyze("run tests", repo_root_known=True)
    assert res.needs_confirm is True
    assert res.state == "NEEDS_INFO"
