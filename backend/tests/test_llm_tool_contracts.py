import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm_client import llm_client


def verify_tool_contracts() -> None:
    decision_tool = llm_client._agent_decision_tool(["worker1Marker"])
    decision_function = decision_tool["function"]
    assert decision_function["name"] == "office_agent_decision"
    assert decision_function["parameters"]["properties"]["movement_type"]["enum"] == [
        "own_desk",
        "visit_colleague",
        "meeting",
        "break",
        "stay",
    ]
    assert "target_id" in decision_function["parameters"]["properties"]

    meeting_tool = llm_client._meeting_reply_tool()
    meeting_function = meeting_tool["function"]
    assert meeting_function["name"] == "meeting_reply"
    assert meeting_function["parameters"]["required"] == ["reply"]

    planning_tool = llm_client._project_plan_item_tool()
    planning_function = planning_tool["function"]
    assert planning_function["name"] == "project_plan_item"
    required = set(planning_function["parameters"]["required"])
    assert required == {"contribution", "task_title", "task_type", "assignee_hint", "risk_note"}
    assert planning_function["parameters"]["properties"]["task_type"]["enum"] == [
        "product",
        "backend",
        "frontend",
        "design",
        "qa",
        "data",
        "ops",
        "general",
    ]

    route_tool = llm_client._directive_route_tool()
    route_function = route_tool["function"]
    assert route_function["name"] == "office_directive_route"
    assert route_function["parameters"]["properties"]["route"]["enum"] == ["meeting", "work"]
    assert set(route_function["parameters"]["required"]) == {"route", "confidence", "reason"}

    colleague_tool = llm_client._colleague_reply_tool()
    colleague_function = colleague_tool["function"]
    assert colleague_function["name"] == "colleague_reply"
    assert set(colleague_function["parameters"]["required"]) == {
        "reply",
        "work_update",
        "risk_note",
        "next_step",
        "confidence",
    }


if __name__ == "__main__":
    verify_tool_contracts()
    print("llm tool contract checks passed")
