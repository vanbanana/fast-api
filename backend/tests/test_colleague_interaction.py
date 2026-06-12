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
        {"id": "worker3Marker", "group": "seat_markers"},
        {"id": "desk1", "group": "supMarkers"},
    ]


async def verify_colleague_interaction() -> None:
    old_llm_enabled = settings.llm_enabled
    settings.llm_enabled = False
    runtime = OfficeRuntime()
    try:
        await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
        seeker = runtime.agents["worker1"]
        helper = runtime.agents["worker2"]
        task = runtime.company.create_task("确认教育项目登录接口边界", "backend", 4, "test", "worker1")
        seeker.active_task_id = task.task_id
        seeker.focus_task = task.title
        seeker.current_directive = "做教育项目登录"
        seeker.confirmation_question = "小周，登录接口返回字段和错误码按什么口径定？"
        assert seeker.fsm.start_seeking("worker2")
        helper.last_target_id = "worker2Marker"

        commands = await runtime.handle_event(
            WorkerEvent(type="worker_arrived", worker_id="worker1", target_id="worker2Marker")
        )
        assert len(commands) == 1
        command = commands[0]
        assert command.action == "say"
        assert command.payload.get("display") == "speech"
        assert command.payload.get("behavior_state") == "collaboration_loop"
        assert "林主管" in command.say
        assert seeker.fsm.helper_id == ""
        assert seeker.fsm.state.value == "collaborating"
        assert seeker.needs_help_from == ""
        assert helper.status.startswith("回应")
        assert any("协作回应" in item for item in helper.memory)

        helper_commands = await runtime.handle_event(WorkerEvent(type="worker_ready", worker_id="worker2"))
        assert len(helper_commands) == 1
        helper_command = helper_commands[0]
        assert helper_command.worker_id == "worker2"
        assert helper_command.action == "say"
        assert helper_command.payload.get("display") == "speech"
        assert helper_command.payload.get("behavior_state") == "collaboration_reply_loop"
        assert "小周" in helper_command.say
        assert helper.fsm.pending_reply.is_empty()
    finally:
        settings.llm_enabled = old_llm_enabled


async def main() -> None:
    await verify_colleague_interaction()
    print("colleague interaction checks passed")


if __name__ == "__main__":
    asyncio.run(main())
