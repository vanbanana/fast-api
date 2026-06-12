import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.domain import CompanyProject
from app.runtime import OfficeRuntime
from app.schemas import WorkerEvent


def _targets() -> list[dict[str, str]]:
    return [
        {"id": "worker2Marker", "group": "seat_markers"},
        {"id": "worker6Marker", "group": "seat_markers"},
        {"id": "desk1", "group": "supMarkers"},
    ]


def verify_review_transitions() -> None:
    """开发类任务进度满后进入 review，不直接 done；打回后回到 doing。"""
    company = CompanyProject()
    task = company.create_task("登录接口", "backend", 3, "test", "worker2")
    completed = task.advance(1.0, "一次推满")
    assert completed is False
    assert task.status == "review"
    assert task.review_state == "pending"
    assert company.task_for_agent("worker2") is None
    assert company.next_review_task() is task

    task.fail_review("测试打回")
    assert task.status == "doing"
    assert task.progress <= 0.72
    assert task.rework_count == 1
    assert company.task_for_agent("worker2") is task

    task.advance(1.0, "返工推满")
    assert task.status == "review"
    task.pass_review("验收通过")
    assert task.status == "done"
    assert task.review_state == "passed"


def verify_non_dev_task_skips_review() -> None:
    company = CompanyProject()
    task = company.create_task("制定验收用例", "qa", 3, "test", "worker6")
    completed = task.advance(1.0, "一次推满")
    assert completed is True
    assert task.status == "done"


async def verify_qa_review_loop() -> None:
    """测试工程师在工位 tick 时验收提测任务，产出 say 指令并更新任务状态。"""
    old_llm_enabled = settings.llm_enabled
    settings.llm_enabled = False
    runtime = OfficeRuntime()
    try:
        await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
        qa = runtime.agents["worker6"]
        qa.last_target_id = "worker6Marker"
        task = runtime.company.create_task("登录接口", "backend", 3, "test", "worker2")
        task.advance(1.0, "推满提测")
        assert task.status == "review"

        commands = await runtime.handle_event(WorkerEvent(type="worker_ready", worker_id="worker6"))
        assert len(commands) == 1
        command = commands[0]
        assert command.action == "say"
        assert command.payload.get("behavior_state") == "qa_review_loop"
        assert command.say.strip() != ""
        assert task.status in ("done", "doing")
        if task.status == "doing":
            assert task.rework_count == 1
    finally:
        settings.llm_enabled = old_llm_enabled


async def main() -> None:
    verify_review_transitions()
    verify_non_dev_task_skips_review()
    await verify_qa_review_loop()
    print("task lifecycle checks passed")


if __name__ == "__main__":
    asyncio.run(main())
