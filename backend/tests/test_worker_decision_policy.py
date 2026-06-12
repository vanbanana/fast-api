import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain import OfficeTargets
from app.worker_decision_policy import enforce_fixed_workstation_target, llm_work_targets, normalize_colleague_id, worker_id_from_desk_marker


@dataclass
class FakeWorker:
    worker_id: str
    name: str
    role: str
    assigned_meeting_seat: str = ""
    seeking_helper_id: str = ""
    checked_helper_desk: bool = False


def verify_policy() -> None:
    targets = OfficeTargets(
        seats=["worker1Marker", "worker2Marker", "leftTopChair"],
        roam_points=["desk1"],
    )
    worker1 = FakeWorker("worker1", "林主管", "项目经理")
    worker2 = FakeWorker("worker2", "小周", "后端工程师")
    agents = {"worker1": worker1, "worker2": worker2}

    assert llm_work_targets(worker1, targets) == ["worker1Marker", "desk1"]
    assert "leftTopChair" not in llm_work_targets(worker1, targets)

    data = {"target_id": "leftTopChair"}
    mode = enforce_fixed_workstation_target(worker1, data, targets, "normal")
    assert mode == "normal"
    assert data["target_id"] == "worker1Marker"

    data = {"target_id": "worker2Marker"}
    mode = enforce_fixed_workstation_target(worker1, data, targets, "normal")
    assert mode == "visit"
    assert data["needs_help_from"] == "worker2"
    assert worker1.seeking_helper_id == "worker2"

    worker1.assigned_meeting_seat = "leftTopChair"
    data = {"target_id": "leftTopChair"}
    assert enforce_fixed_workstation_target(worker1, data, targets, "normal") == "meeting"

    assert normalize_colleague_id("小周", "worker1", agents) == "worker2"
    assert normalize_colleague_id("后端", "worker1", agents) == "worker2"
    assert worker_id_from_desk_marker("worker2Marker") == "worker2"
    assert worker_id_from_desk_marker("leftTopChair") == ""


if __name__ == "__main__":
    verify_policy()
    print("worker decision policy checks passed")
