from dataclasses import dataclass, field
from time import time
from typing import Any

from app.schemas import AgentSnapshot, CompanySnapshot, ProjectTaskSnapshot


@dataclass
class OfficeTargets:
    """由 Godot 场景上报的可用目标，后端不写死场景节点或坐标。"""

    seats: list[str] = field(default_factory=list)
    idle_points: list[str] = field(default_factory=list)
    roam_points: list[str] = field(default_factory=list)

    def all_targets(self) -> list[str]:
        return self.seats + self.idle_points + self.roam_points

    def work_targets(self) -> list[str]:
        return self.desk_markers() + self.meeting_seats() + self.roam_points

    def desk_markers(self) -> list[str]:
        return [target_id for target_id in self.seats if target_id.startswith("worker") and target_id.endswith("Marker")]

    def own_desk(self, worker_id: str) -> str | None:
        desk_id = f"{worker_id}Marker"
        if desk_id in self.seats:
            return desk_id
        return None

    def meeting_seats(self) -> list[str]:
        return [target_id for target_id in self.seats if "Chair" in target_id]


@dataclass
class BossDirective:
    """玩家老板的一条管理指令。"""

    text: str
    priority: int
    target_worker_ids: list[str]
    created_at: float = field(default_factory=time)

    def applies_to(self, worker_id: str) -> bool:
        return not self.target_worker_ids or worker_id in self.target_worker_ids

    def is_meeting_request(self) -> bool:
        # 兼容旧调用；真实路由请使用 directive_router.route_directive。
        meeting_words = ["去会议室", "会议室", "开会", "召开会议", "进会议", "meeting room"]
        return any(word in self.text for word in meeting_words)


@dataclass
class ProjectTask:
    """软件公司看板上的一个真实工作任务。"""

    task_id: str
    title: str
    task_type: str
    priority: int
    created_by: str
    status: str = "todo"
    assignee_id: str | None = None
    progress: float = 0.0
    notes: list[str] = field(default_factory=list)
    review_state: str = ""
    rework_count: int = 0

    def assign_to(self, worker_id: str) -> None:
        self.assignee_id = worker_id
        if self.status == "todo":
            self.status = "doing"

    def advance(self, amount: float, note: str) -> bool:
        if self.status in ("done", "review"):
            return False

        self.status = "doing"
        self.progress = min(1.0, self.progress + amount)
        self.notes.append(note)
        self.notes = self.notes[-8:]
        if self.progress >= 1.0:
            if self.needs_review():
                self.status = "review"
                self.review_state = "pending"
                return False
            self.status = "done"
            return True
        return False

    def needs_review(self) -> bool:
        """审核流程暂未实现，所有任务到100%直接完成。"""
        return False

    def fail_review(self, note: str) -> None:
        self.status = "doing"
        self.progress = min(self.progress, 0.72)
        self.rework_count += 1
        self.review_state = ""
        self.notes.append(note)
        self.notes = self.notes[-8:]

    def pass_review(self, note: str) -> None:
        self.status = "done"
        self.review_state = "passed"
        self.notes.append(note)
        self.notes = self.notes[-8:]

    def snapshot(self) -> ProjectTaskSnapshot:
        return ProjectTaskSnapshot(
            task_id=self.task_id,
            title=self.title,
            task_type=self.task_type,
            priority=self.priority,
            status=self.status,
            assignee_id=self.assignee_id,
            progress=round(self.progress, 3),
            created_by=self.created_by,
            notes=self.notes[-5:],
        )


@dataclass
class CompanyProject:
    """公司项目状态，承载 sprint、任务和风险等公司级信息。"""

    project_name: str = "Purr-formance SaaS"
    day: int = 1
    sprint_goal: str = "做出可玩的办公室模拟原型"
    morale: float = 0.72
    release_risk: float = 0.35
    next_task_index: int = 1
    tasks: dict[str, ProjectTask] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 开局不预置任务。玩家输入 boss 指令后再生成真实任务，空闲时员工自然闲逛。
        return

    def create_task(
        self,
        title: str,
        task_type: str,
        priority: int,
        created_by: str,
        assignee_id: str | None = None,
    ) -> ProjectTask:
        task_id = f"T{self.next_task_index:03d}"
        self.next_task_index += 1
        task = ProjectTask(
            task_id=task_id,
            title=title,
            task_type=task_type,
            priority=priority,
            created_by=created_by,
            assignee_id=assignee_id,
            status="doing" if assignee_id else "todo",
        )
        self.tasks[task_id] = task
        return task

    def task_for_agent(self, worker_id: str) -> ProjectTask | None:
        for task in self.tasks.values():
            if task.assignee_id == worker_id and task.status not in ("done", "review"):
                return task
        return None

    def next_review_task(self) -> ProjectTask | None:
        candidates = [task for task in self.tasks.values() if task.status == "review"]
        candidates.sort(key=lambda item: (-item.priority, item.task_id))
        return candidates[0] if candidates else None

    def best_unassigned_task_for_role(self, role: str) -> ProjectTask | None:
        role_keywords = {
            "产品": "product",
            "后端": "backend",
            "架构": "backend",
            "UI": "design",
            "设计": "design",
            "测试": "qa",
            "数据": "data",
            "运营": "ops",
            "HR": "people",
        }
        preferred_type = "general"
        for keyword, task_type in role_keywords.items():
            if keyword in role:
                preferred_type = task_type
                break

        candidates = [task for task in self.tasks.values() if task.assignee_id is None and task.status != "done"]
        candidates.sort(key=lambda item: (item.task_type != preferred_type, -item.priority, item.task_id))
        return candidates[0] if candidates else None

    def assign_directive_task(self, directive: BossDirective, agents: dict[str, Any]) -> None:
        target_ids = directive.target_worker_ids or list(agents.keys())
        task_type = self._infer_task_type(directive.text)
        for worker_id in target_ids:
            if worker_id not in agents:
                continue
            task = self.create_task(directive.text, task_type, directive.priority, "boss", worker_id)
            agents[worker_id].active_task_id = task.task_id

    def advance_task(self, task_id: str, worker_name: str, amount: float) -> bool:
        task = self.tasks.get(task_id)
        if task is None:
            return False
        completed = task.advance(amount, f"{worker_name} 推进了 {amount:.2f}")
        self._recalculate_company_health()
        return completed

    def snapshot(self, agents: list[AgentSnapshot]) -> CompanySnapshot:
        done_tasks = sum(1 for task in self.tasks.values() if task.status == "done")
        open_tasks = len(self.tasks) - done_tasks
        return CompanySnapshot(
            project_name=self.project_name,
            day=self.day,
            sprint_goal=self.sprint_goal,
            morale=round(self.morale, 3),
            release_risk=round(self.release_risk, 3),
            open_tasks=open_tasks,
            done_tasks=done_tasks,
            agents=agents,
            tasks=[task.snapshot() for task in self.tasks.values()],
        )

    def _infer_task_type(self, text: str) -> str:
        mapping = {
            "需求": "product",
            "产品": "product",
            "bug": "backend",
            "接口": "backend",
            "后端": "backend",
            "界面": "design",
            "UI": "design",
            "测试": "qa",
            "验证": "qa",
            "数据": "data",
            "上线": "ops",
        }
        for keyword, task_type in mapping.items():
            if keyword in text:
                return task_type
        return "general"

    def _recalculate_company_health(self) -> None:
        if not self.tasks:
            return
        done = sum(1 for task in self.tasks.values() if task.status == "done")
        total = len(self.tasks)
        done_ratio = done / total
        high_priority_open = sum(1 for task in self.tasks.values() if task.status != "done" and task.priority >= 4)
        self.release_risk = max(0.05, min(0.95, 0.55 - done_ratio * 0.35 + high_priority_open * 0.04))
        self.morale = max(0.1, min(1.0, 0.65 + done_ratio * 0.25 - high_priority_open * 0.02))
