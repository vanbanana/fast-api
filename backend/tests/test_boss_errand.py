import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.runtime import OfficeRuntime
from app.schemas import WorkerEvent


def _targets() -> list[dict[str, str]]:
    return [
        {"id": "worker1Marker", "group": "seat_markers"},
        {"id": "worker2Marker", "group": "seat_markers"},
        {"id": "worker4Marker", "group": "seat_markers"},
        {"id": "desk1", "group": "supMarkers"},
    ]


async def verify_errand_interrupts_and_resumes() -> None:
    """老板指派「让小周去找老陈」：中断小周手头任务，办完后恢复原任务。"""
    old_llm_enabled = settings.llm_enabled
    settings.llm_enabled = False
    runtime = OfficeRuntime()
    try:
        await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
        actor = runtime.agents["worker2"]
        helper = runtime.agents["worker4"]
        task = runtime.company.create_task("登录接口边界确认", "backend", 4, "test", "worker2")
        actor.active_task_id = task.task_id
        actor.focus_task = task.title
        helper.last_target_id = "worker4Marker"

        commands = await runtime.handle_event(
            WorkerEvent(type="boss_command", worker_id="boss", payload={"text": "让小周去找老陈确认部署方案", "priority": 4})
        )
        assert len(commands) >= 1
        command = commands[0]
        assert command.worker_id == "worker2"
        assert command.action == "move_to"
        assert command.target_id == "worker4Marker"
        assert actor.fsm.state.value == "seeking"
        assert actor.fsm.helper_id == "worker4"
        assert actor.errand_helper_id == "worker4"
        assert actor.interrupted_task_id == task.task_id
        assert actor.active_task_id == ""

        arrive_commands = await runtime.handle_event(
            WorkerEvent(type="worker_arrived", worker_id="worker2", target_id="worker4Marker")
        )
        assert len(arrive_commands) == 1
        assert arrive_commands[0].action == "say"
        assert actor.fsm.state.value == "collaborating"
        assert actor.errand_helper_id == ""
        assert actor.active_task_id == task.task_id
        assert actor.focus_task == task.title
        assert actor.current_directive == ""
    finally:
        settings.llm_enabled = old_llm_enabled


async def verify_non_errand_directive_unchanged() -> None:
    """普通指令（没点名找人）仍走团队规划，不会被错误识别成指派。"""
    old_llm_enabled = settings.llm_enabled
    settings.llm_enabled = False
    runtime = OfficeRuntime()
    try:
        await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
        matched = runtime._match_errand(type("D", (), {"text": "做一个教育项目的登录功能"})())
        assert matched is None
    finally:
        settings.llm_enabled = old_llm_enabled


async def main() -> None:
    await verify_errand_interrupts_and_resumes()
    await verify_non_errand_directive_unchanged()
    print("boss errand checks passed")


if __name__ == "__main__":
    asyncio.run(main())
