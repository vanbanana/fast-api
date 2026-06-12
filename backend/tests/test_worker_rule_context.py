import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain import ProjectTask
from app.worker_rule_context import build_confirmation_question, build_rule_work_context, should_confirm_with_team, suggest_helper


@dataclass
class FakeWorker:
    worker_id: str
    name: str
    role: str
    current_directive: str = ""
    active_task_id: str = ""
    focus_task: str = "等待明确目标"
    energy: float = 1.0
    stress: float = 0.2


def verify_rule_context() -> None:
    worker = FakeWorker("worker5", "米娅", "UI 设计师")
    idle_context = build_rule_work_context(worker, "desk1", None)
    assert idle_context["say"] == ""
    assert idle_context["intent"] == "没有明确任务，先在办公室自然走动"

    task = ProjectTask(
        task_id="T001",
        title="登录改版要尽快完善",
        task_type="design",
        priority=4,
        created_by="test",
        assignee_id="worker5",
    )
    worker.current_directive = "登录改版"
    worker.active_task_id = "T001"
    context = build_rule_work_context(worker, "worker5Marker", task)
    assert context["intent"] == "回到工位专注推进任务"
    assert "状态和交互" in str(context["say"])
    assert context["confirmation_question"] == "项目经理拉产品、测试和对应负责人把验收标准补齐。"
    assert context["risk_note"] == "团队内部需要先补齐验收口径"

    assert suggest_helper(worker, task) == ""
    backend_task = ProjectTask("T002", "接口字段确认", "backend", 3, "test")
    assert suggest_helper(worker, backend_task) == "worker2"
    assert should_confirm_with_team(task)
    assert build_confirmation_question(backend_task) == "项目经理拉产品、测试和对应负责人把验收标准补齐。"


if __name__ == "__main__":
    verify_rule_context()
    print("worker rule context checks passed")
