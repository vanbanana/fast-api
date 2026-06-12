from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkerEvent(BaseModel):
    """Godot 发来的角色事件。"""

    type: str
    worker_id: str
    target_id: str | None = None
    target_group: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentCommand(BaseModel):
    """后端下发给 Godot 的角色指令。"""

    type: Literal["command"] = "command"
    worker_id: str
    action: Literal["move_to", "say", "idle", "stream_delta"] = "move_to"
    target_id: str | None = None
    say: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class BossCommand(BaseModel):
    """玩家 boss 下达的管理指令。"""

    text: str
    target_worker_ids: list[str] = Field(default_factory=list)
    priority: int = Field(default=2, ge=1, le=5)


class AgentSnapshot(BaseModel):
    """给调试 UI 或网页展示的员工状态。"""

    worker_id: str
    name: str
    role: str
    personality: str
    roleplay_prompt: str
    communication_style: str
    work_values: list[str]
    conflict_triggers: list[str]
    relationship_notes: dict[str, str]
    status: str
    mood: str
    energy: float
    focus_task: str
    current_directive: str
    autonomy_steps: int
    active_task_id: str
    completed_task_count: int
    stress: float
    needs_help_from: str
    current_risk: str
    confirmation_question: str
    memory: list[str]


class ProjectTaskSnapshot(BaseModel):
    """软件公司任务看板里的单个任务。"""

    task_id: str
    title: str
    task_type: str
    priority: int
    status: str
    assignee_id: str | None = None
    progress: float
    created_by: str
    notes: list[str]


class CompanySnapshot(BaseModel):
    """公司层面的模拟状态。"""

    project_name: str
    day: int
    sprint_goal: str
    morale: float
    release_risk: float
    open_tasks: int
    done_tasks: int
    agents: list[AgentSnapshot]
    tasks: list[ProjectTaskSnapshot]
