"""单个员工 agent —— 精简版。

只负责：角色画像、记忆、老板指令接收、快照输出。
行为决策完全交给 Godot 本地状态机 + atmosphere_service。
"""
from dataclasses import dataclass, field

from app.domain import BossDirective, CompanyProject, ProjectTask
from app.memory import memory_store
from app.schemas import AgentSnapshot, WorkerEvent


@dataclass
class OfficeAgent:
    """单个员工的轻量数据容器。行为逻辑在 Godot 端，这里只存状态和数据。"""
    worker_id: str
    name: str
    role: str
    personality: str
    work_style: str = ""
    communication_style: str = ""
    work_values: list[str] = field(default_factory=list)
    conflict_triggers: list[str] = field(default_factory=list)
    relationship_notes: dict[str, str] = field(default_factory=dict)
    roleplay_template: str = ""

    # 运行时状态（被 Godot/atmosphere_service 读写）
    status: str = "在办公室待命"
    mood: str = "平稳"
    energy: float = 1.0
    stress: float = 0.18
    focus_task: str = "熟悉今天的工作"
    current_directive: str = ""
    active_task_id: str = ""
    completed_task_count: int = 0
    needs_help_from: str = ""
    current_risk: str = ""
    confirmation_question: str = ""
    assigned_meeting_seat: str = ""
    errand_helper_id: str = ""
    interrupted_task_id: str = ""
    interrupted_focus_task: str = ""
    memory: list[str] = field(default_factory=list)

    def remember(self, text: str) -> None:
        if not memory_store.should_store(text):
            return
        if text in self.memory[-10:]:
            return
        self.memory.append(text)
        self.memory = self.memory[-20:]  # 缩短窗口
        memory_store.remember(self.worker_id, text)

    def reset_runtime_state(self) -> None:
        self.status = "在办公室待命"
        self.mood = "平稳"
        self.focus_task = "等待明确目标"
        self.current_directive = ""
        self.active_task_id = ""
        self.needs_help_from = ""
        self.current_risk = ""
        self.confirmation_question = ""
        self.assigned_meeting_seat = ""
        self.errand_helper_id = ""
        self.interrupted_task_id = ""
        self.interrupted_focus_task = ""
        self.energy = 1.0
        self.stress = 0.18

    def apply_directive(self, directive: BossDirective, task: ProjectTask | None = None) -> None:
        self.current_directive = directive.text
        self.focus_task = directive.text
        if task:
            self.active_task_id = task.task_id
        self.status = "执行老板指令"
        self.mood = "被关注"
        self.stress = min(1.0, self.stress + 0.04 * directive.priority)
        if self.name in directive.text:
            self.remember(f"老板指令:{directive.text}")

    def interrupt_for_errand(self, directive: BossDirective, helper_id: str) -> None:
        if self.active_task_id:
            self.interrupted_task_id = self.active_task_id
            self.interrupted_focus_task = self.focus_task
        self.errand_helper_id = helper_id
        self.current_directive = directive.text
        self.focus_task = directive.text
        self.confirmation_question = directive.text[:100]
        self.active_task_id = ""
        self.status = "执行老板指派"
        self.mood = "被点名"
        self.remember(f"老板指派:{directive.text}")

    def finish_errand(self) -> None:
        if not self.errand_helper_id:
            return
        self.errand_helper_id = ""
        self.current_directive = ""
        self.confirmation_question = ""
        if self.interrupted_task_id:
            self.active_task_id = self.interrupted_task_id
            self.focus_task = self.interrupted_focus_task or self.focus_task
            self.status = "回到原任务"
            self.remember(f"办完老板指派，回到原任务:{self.focus_task}")
        else:
            self.status = "在办公室待命"
        self.interrupted_task_id = ""
        self.interrupted_focus_task = ""

    def roleplay_prompt(self) -> str:
        if self.roleplay_template:
            return self.roleplay_template
        return (
            f"你正在扮演 {self.name}，员工ID为 {self.worker_id}，岗位是 {self.role}。"
            f"你的性格：{self.personality}。"
            f"你的工作方式：{self.work_style}。"
            f"你的沟通风格：{self.communication_style}。"
            f"你重视：{self.work_values}。"
            f"你容易被这些情况触发压力或冲突：{self.conflict_triggers}。"
            f"你和同事的关系与协作线索：{self.relationship_notes}。"
        )

    def snapshot(self) -> AgentSnapshot:
        return AgentSnapshot(
            worker_id=self.worker_id,
            name=self.name,
            role=self.role,
            personality=self.personality,
            roleplay_prompt=self.roleplay_prompt(),
            communication_style=self.communication_style,
            work_values=self.work_values,
            conflict_triggers=self.conflict_triggers,
            relationship_notes=self.relationship_notes,
            status=self.status,
            mood=self.mood,
            energy=round(self.energy, 3),
            focus_task=self.focus_task,
            current_directive=self.current_directive,
            autonomy_steps=0,  # 兼容旧 schema，实际不用了
            active_task_id=self.active_task_id,
            completed_task_count=self.completed_task_count,
            stress=round(self.stress, 3),
            needs_help_from=self.needs_help_from,
            current_risk=self.current_risk,
            confirmation_question=self.confirmation_question,
            memory=memory_store.display_memory(self.worker_id, 6),
        )
