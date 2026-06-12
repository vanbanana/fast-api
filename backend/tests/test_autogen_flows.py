import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
        "worker9Marker",
        "worker10Marker",
        "worker11Marker",
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
    for target_id in ["desk1", "desk2", "desk3", "desk4", "desk5", "desk6"]:
        targets.append({"id": target_id, "group": "supMarkers"})
    return targets


async def _runtime() -> OfficeRuntime:
    runtime = OfficeRuntime()
    await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
    return runtime


async def verify_project_planning() -> None:
    runtime = await _runtime()
    text = "做一个教育类项目，要支持学生登录、课程展示和学习进度跟踪"
    commands = await runtime.apply_boss_command(BossCommand(text=text, priority=4))
    task_types = {task.task_type for task in runtime.company.tasks.values()}
    assignees = {task.assignee_id for task in runtime.company.tasks.values()}

    assert len(runtime.company.tasks) >= 6, "普通项目目标应该被拆成多个岗位任务"
    assert {"product", "backend", "qa"}.issubset(task_types), f"缺少关键任务类型: {task_types}"
    assert len(assignees) >= 6, f"任务不应该集中到少数人: {assignees}"
    assert all(task.created_by == "autogen_team" for task in runtime.company.tasks.values())
    assert commands, "被分配任务的员工应该收到行动命令"
    assert all(not str(command.target_id).endswith("Chair") for command in commands), "普通工作不能去会议椅"
    for command in commands:
        if command.action != "move_to" or not command.target_id:
            continue
        if str(command.target_id).startswith("worker") and str(command.target_id).endswith("Marker"):
            is_own_desk = command.target_id == f"{command.worker_id}Marker"
            is_visit = command.payload.get("travel_mode") == "visit"
            assert is_own_desk or is_visit, f"{command.worker_id} 不能坐到别人工位: {command.target_id}"


async def verify_meeting_flow() -> None:
    runtime = await _runtime()
    text = "去会议室讨论一下一个教育类项目问题"
    move_commands = await runtime.apply_boss_command(BossCommand(text=text, priority=4))
    assert len(move_commands) == 8, "会议应该选择 8 个参会人"
    assert runtime.active_meeting is not None
    assert runtime.active_meeting.turns_task is not None, "会议入座路上应该已经后台预取 transcript"
    assert all(command.target_id and "Chair" in command.target_id for command in move_commands)
    assert all(command.say == "" for command in move_commands), "入座阶段不应该复读台词"
    assert all(command.payload.get("travel_mode") == "meeting" for command in move_commands), "会议入座必须走 meeting 模式"

    reseat = await runtime.handle_event(WorkerEvent(type="worker_ready", worker_id=move_commands[0].worker_id))
    assert reseat and reseat[0].action == "move_to", "会议未到座时必须补发入座命令，不能只 idle"
    assert reseat[0].target_id == move_commands[0].target_id
    assert reseat[0].say == ""
    assert reseat[0].payload.get("decision_source") == "meeting_reseat"

    first_say = []
    for command in move_commands:
        result = await runtime.handle_event(
            WorkerEvent(type="worker_arrived", worker_id=command.worker_id, target_id=command.target_id)
        )
        if result:
            first_say.extend(result)

    assert first_say and first_say[0].action == "say", "全员到齐后应该开始会议发言"
    assert runtime.active_meeting is not None
    assert runtime.active_meeting.pending_turns, "会议应该有待播放 transcript"

    locked = await runtime.handle_event(WorkerEvent(type="worker_ready", worker_id=move_commands[0].worker_id))
    assert locked and locked[0].action == "idle", "会议中参会人不能被普通自主决策抢走"
    assert locked[0].say == "", "会议等待状态不应该冒泡"

    participant_ids = {command.worker_id for command in move_commands}
    non_participant_id = next(worker_id for worker_id in runtime.agents if worker_id not in participant_ids)
    outside_commands = await runtime.handle_event(WorkerEvent(type="worker_ready", worker_id=non_participant_id))
    assert outside_commands, "非参会人仍然应该能走普通工作/闲逛逻辑"
    assert all(command.payload.get("decision_source") != "meeting_lock" for command in outside_commands)
    assert all(not str(command.target_id).endswith("Chair") for command in outside_commands if command.target_id), "非参会人的工作逻辑不能套进会议椅"

    last_speaker = first_say[0].worker_id
    finish_commands = []
    while runtime.active_meeting is not None:
        result = await runtime.handle_event(
            WorkerEvent(type="meeting_say_done", worker_id=last_speaker, payload={"session_id": runtime.active_meeting.session_id})
        )
        if result and result[0].action == "say":
            last_speaker = result[0].worker_id
        else:
            finish_commands = result

    assert finish_commands, "会议结束后应该统一派发回工位命令"
    assert all(command.action == "move_to" for command in finish_commands)
    assert all(command.target_id == f"{command.worker_id}Marker" for command in finish_commands)
    assert all(command.payload.get("decision_source") == "meeting_finished" for command in finish_commands)


async def main() -> None:
    await verify_project_planning()
    await verify_meeting_flow()
    print("autogen flow checks passed")


if __name__ == "__main__":
    asyncio.run(main())
