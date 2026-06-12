import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.runtime import OfficeRuntime
from app.schemas import BossCommand, WorkerEvent


def _targets() -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for target_id in [
        "worker1Marker",
        "worker2Marker",
        "worker3Marker",
        "worker4Marker",
        "worker5Marker",
        "worker6Marker",
        "worker7Marker",
        "worker8Marker",
        "leftTopChair",
        "rightTopChair",
        "leftUpperChair",
        "rightUpperChair",
        "leftLowerChair",
        "rightLowerChair",
        "leftBottomChair",
        "rightBottomChair",
    ]:
        targets.append({"id": target_id, "group": "seat_markers"})
    targets.append({"id": "desk1", "group": "supMarkers"})
    return targets


async def _runtime() -> OfficeRuntime:
    runtime = OfficeRuntime()
    await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
    return runtime


async def verify_routing_flow() -> None:
    old_llm_enabled = settings.llm_enabled
    settings.llm_enabled = False
    try:
        runtime = await _runtime()
        work_commands = await runtime.apply_boss_command(
            BossCommand(text="讨论一下一个教育类项目怎么做，团队自己拆需求开发测试上线", priority=4)
        )
        assert work_commands
        assert runtime.active_meeting is None
        assert all(not str(command.target_id).endswith("Chair") for command in work_commands if command.target_id)
        assert all(command.payload.get("directive_route", {}).get("route") == "work" for command in work_commands)

        runtime = await _runtime()
        meeting_commands = await runtime.apply_boss_command(
            BossCommand(text="去会议室讨论一下一个教育类项目问题", priority=4)
        )
        assert runtime.active_meeting is not None
        assert len(meeting_commands) == 8
        assert all(str(command.target_id).endswith("Chair") for command in meeting_commands)
        assert all(command.payload.get("directive_route", {}).get("route") == "meeting" for command in meeting_commands)
    finally:
        settings.llm_enabled = old_llm_enabled


async def main() -> None:
    await verify_routing_flow()
    print("directive routing flow checks passed")


if __name__ == "__main__":
    asyncio.run(main())
