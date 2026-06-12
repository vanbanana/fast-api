import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.runtime import OfficeRuntime
from app.schemas import WorkerEvent


def _targets() -> list[dict[str, str]]:
    return [
        {"id": "worker1Marker", "group": "seat_markers"},
        {"id": "worker2Marker", "group": "seat_markers"},
        {"id": "leftTopChair", "group": "seat_markers"},
        {"id": "water1", "group": "idle_markers"},
        {"id": "desk1", "group": "supMarkers"},
    ]


async def verify_work_reaction_loop() -> None:
    runtime = OfficeRuntime()
    await runtime.handle_event(WorkerEvent(type="world_snapshot", worker_id="office", payload={"targets": _targets()}))
    agent = runtime.agents["worker1"]
    task = runtime.company.create_task("整理教育项目排期和风险", "ops", 4, "test", "worker1")
    agent.current_directive = "做一个教育项目"
    agent.active_task_id = task.task_id
    agent.focus_task = task.title

    arrived = await runtime.handle_event(WorkerEvent(type="worker_arrived", worker_id="worker1", target_id="worker1Marker"))
    assert len(arrived) == 1
    assert arrived[0].action == "idle", "到达本人固定工位后应先工作停留，不应立刻重新 move_to"
    assert arrived[0].target_id is None
    assert arrived[0].payload.get("decision_source") == "reaction_loop"
    assert arrived[0].payload.get("behavior_state") == "work_loop"
    first_progress = runtime.company.tasks[task.task_id].progress
    assert first_progress > 0

    ready = await runtime.handle_event(WorkerEvent(type="worker_ready", worker_id="worker1"))
    assert len(ready) == 1
    assert ready[0].action == "idle"
    assert ready[0].payload.get("decision_source") == "reaction_loop"
    assert runtime.company.tasks[task.task_id].progress > first_progress


async def main() -> None:
    await verify_work_reaction_loop()
    print("agent reaction loop checks passed")


if __name__ == "__main__":
    asyncio.run(main())
