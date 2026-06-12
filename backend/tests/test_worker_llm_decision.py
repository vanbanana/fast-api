import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.worker_llm_decision import agent_stream_lines, clean_visible_text, normalize_llm_decision, safe_confidence


def verify_llm_decision_helpers() -> None:
    assert clean_visible_text("我先确认接口。<tool_call>{bad}") == "我先确认接口。"
    assert clean_visible_text("好的，，我处理。。") == "好的，我处理。"

    data = normalize_llm_decision(
        {
            "say": "我去找小周<parameter=bad>",
            "confirmation_question": "<parameter=tool_noise>",
            "stream_lines": ["确认接口字段", "<tool_call>bad", "推进后端联调"],
        },
        None,
    )
    assert data["say"] == "我去找小周"
    assert data["confirmation_question"] == ""
    assert data["stream_lines"] == ["确认接口字段", "推进后端联调"]

    lines = agent_stream_lines(
        {
            "intent": "确认接口",
            "work_update": "整理字段",
            "risk_note": "字段不清",
            "needs_help_from": "worker2",
            "confirmation_question": "接口返回字段是什么",
        },
        "我去找小周",
    )
    assert any(line.startswith("判断:") for line in lines)
    assert any(line.startswith("推进:") for line in lines)
    assert any(line.startswith("风险:") for line in lines)
    assert any(line.startswith("协作:") for line in lines)
    assert any(line.startswith("待确认:") for line in lines)
    assert lines[-1] == "台词: 我去找小周"

    assert safe_confidence("2") == 1.0
    assert safe_confidence("-1") == 0.0
    assert safe_confidence("bad") == 0.0


if __name__ == "__main__":
    verify_llm_decision_helpers()
    print("worker llm decision checks passed")
