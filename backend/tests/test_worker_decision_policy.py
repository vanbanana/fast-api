import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.worker_decision_policy import normalize_colleague_id, worker_id_from_desk_marker


@dataclass
class FakeWorker:
    worker_id: str
    name: str
    role: str


def verify_policy() -> None:
    worker1 = FakeWorker("worker1", "林主管", "项目经理")
    worker2 = FakeWorker("worker2", "小周", "后端工程师")
    agents = {"worker1": worker1, "worker2": worker2}

    assert normalize_colleague_id("worker2", "worker1", agents) == "worker2"
    assert normalize_colleague_id("小周", "worker1", agents) == "worker2"
    assert normalize_colleague_id("后端", "worker1", agents) == "worker2"
    assert normalize_colleague_id("项目经理", "worker2", agents) == "worker1"
    assert normalize_colleague_id("不存在的人", "worker1", agents) == ""
    assert normalize_colleague_id("", "worker1", agents) == ""

    assert worker_id_from_desk_marker("worker2Marker") == "worker2"
    assert worker_id_from_desk_marker("leftTopChair") == ""


if __name__ == "__main__":
    verify_policy()
    print("worker decision policy checks passed")
