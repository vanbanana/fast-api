import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain import CompanyProject, OfficeTargets
from app.worker_behavior_tree import (
    STATE_BREAK,
    STATE_FREE_ROAM,
    STATE_MEETING_ASSIGNED,
    STATE_WORK_AT_DESK,
    choose_rule_behavior,
)


@dataclass
class FakeWorker:
    worker_id: str = "worker1"
    current_directive: str = ""
    active_task_id: str = ""
    assigned_meeting_seat: str = ""
    energy: float = 1.0
    stress: float = 0.1
    personality: str = ""
    work_style: str = ""


def verify_behavior_tree() -> None:
    targets = OfficeTargets(
        seats=["worker1Marker", "worker2Marker", "leftTopChair"],
        idle_points=["water1"],
        roam_points=["desk1", "office1"],
    )
    company = CompanyProject()

    worker = FakeWorker(assigned_meeting_seat="leftTopChair")
    decision = choose_rule_behavior(worker, targets, company, rng=random.Random(0))
    assert decision is not None
    assert decision.state == STATE_MEETING_ASSIGNED
    assert decision.target_id == "leftTopChair"
    assert decision.travel_mode == "meeting"

    worker = FakeWorker()
    decision = choose_rule_behavior(worker, targets, company, rng=random.Random(0))
    assert decision is not None
    assert decision.state == STATE_FREE_ROAM
    assert decision.target_id in targets.roam_points
    assert "Chair" not in decision.target_id

    task = company.create_task("实现教育项目登录接口", "backend", 4, "test", "worker1")
    worker = FakeWorker(current_directive="做一个教育项目", active_task_id=task.task_id)
    decision = choose_rule_behavior(worker, targets, company, rng=random.Random(0))
    assert decision is not None
    assert decision.state == STATE_WORK_AT_DESK
    assert decision.target_id == "worker1Marker"
    assert decision.travel_mode == "normal"

    worker = FakeWorker(current_directive="继续推进任务", active_task_id=task.task_id, energy=0.1)
    decision = choose_rule_behavior(worker, targets, company, rng=random.Random(0))
    assert decision is not None
    assert decision.state == STATE_BREAK
    assert decision.target_id == "water1"
    assert decision.travel_mode == "normal"


if __name__ == "__main__":
    verify_behavior_tree()
    print("worker behavior tree checks passed")
