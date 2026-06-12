from typing import Protocol

from app.domain import OfficeTargets


class WorkerLike(Protocol):
    worker_id: str
    name: str
    role: str
    assigned_meeting_seat: str
    seeking_helper_id: str
    checked_helper_desk: bool


def llm_work_targets(worker: WorkerLike, targets: OfficeTargets) -> list[str]:
    """普通工作候选不含会议椅；会议椅只能由会议运行态分配。"""
    own_desk = targets.own_desk(worker.worker_id)
    work_targets: list[str] = []
    if own_desk:
        work_targets.append(own_desk)
    work_targets.extend(targets.roam_points)
    return work_targets


def enforce_fixed_workstation_target(worker: WorkerLike, data: dict[str, object], targets: OfficeTargets, travel_mode: str) -> str:
    """把模型目标收束到固定工位/拜访/会议三种安全路径。"""
    target_id = str(data.get("target_id", "") or "").strip()
    own_desk = targets.own_desk(worker.worker_id)
    if worker.assigned_meeting_seat and target_id == worker.assigned_meeting_seat:
        return "meeting"
    if target_id in targets.meeting_seats():
        if own_desk:
            data["target_id"] = own_desk
        return "normal"
    other_worker_id = worker_id_from_desk_marker(target_id)
    if other_worker_id and other_worker_id != worker.worker_id:
        data["needs_help_from"] = other_worker_id
        worker.seeking_helper_id = other_worker_id
        worker.checked_helper_desk = False
        return "visit"
    if target_id.endswith("Marker") and target_id != own_desk and own_desk:
        data["target_id"] = own_desk
        return "normal"
    return travel_mode


def normalize_colleague_id(value: str, self_worker_id: str, agents: dict[str, WorkerLike]) -> str:
    """把模型输出的姓名、岗位或 worker_id 统一成 Godot 可寻路的员工 ID。"""
    cleaned = value.strip().replace("：", "").replace(":", "").replace("，", "").replace(",", "")
    if not cleaned:
        return ""
    if cleaned in agents:
        return cleaned

    for worker_id, agent in agents.items():
        if worker_id == self_worker_id:
            continue
        if cleaned == agent.name or cleaned in agent.name:
            return worker_id

    role_priority = [
        ("项目经理", "worker1"),
        ("主管", "worker1"),
        ("产品", "worker3"),
        ("后端", "worker2"),
        ("服务端", "worker2"),
        ("架构", "worker4"),
        ("前端", "worker9"),
        ("UI", "worker5"),
        ("设计", "worker5"),
        ("测试", "worker6"),
        ("QA", "worker6"),
        ("数据", "worker8"),
        ("运营", "worker7"),
        ("HR", "worker11"),
        ("人事", "worker11"),
        ("实习", "worker10"),
    ]
    for keyword, worker_id in role_priority:
        if keyword in cleaned and worker_id in agents and worker_id != self_worker_id:
            return worker_id
    return cleaned if cleaned in agents else ""


def worker_id_from_desk_marker(target_id: str) -> str:
    if not target_id.startswith("worker") or not target_id.endswith("Marker"):
        return ""
    return target_id.removesuffix("Marker")
