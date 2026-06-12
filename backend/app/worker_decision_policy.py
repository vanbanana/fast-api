"""模型输出到员工 ID 的归一化。

目标选择已由 worker_intent.resolve_decision 统一负责，
这里只保留把姓名/岗位/工位 Marker 收敛成 worker_id 的工具。
"""
from typing import Protocol


class WorkerLike(Protocol):
    worker_id: str
    name: str
    role: str


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
