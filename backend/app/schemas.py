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
    action: Literal["move_to", "say", "idle", "status", "stream_delta",
               "errand_seek", "atmosphere_response", "token_usage",
               "llm_log", "chat_line", "chat_end", "chat_canceled",
               "task_update"] = "move_to"
    target_id: str | None = None
    say: str = ""
    status: str = ""
    display_name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class A2AEvent(BaseModel):
    """Godot 发来的 A2A 对话事件。"""
    event: Literal["chat_started", "chat_turn", "chat_timeout"]
    session_id: str = ""
    speaker_id: str = ""      # 发起人 worker_id
    listener_id: str = ""     # 被找的人 worker_id
    directive_text: str = ""  # 老板原始指令
    last_sayer_id: str = ""   # 上一轮说话的人
    last_text: str = ""       # 上一轮的台词
    transcript: str = ""      # 完整对话摘要（最近4轮）


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


class AtmosphereRequest(BaseModel):
    """Godot 上报的员工状态，用于请求氛围数据。"""
    worker_id: str
    name: str
    role: str
    personality: str
    state: str
    location: str
    nearby_workers: list[str] = Field(default_factory=list)
    last_event: str = ""
    current_task: str = ""
    energy: float = 1.0
    stress: float = 0.0


class AtmosphereResponse(BaseModel):
    """后端返回的氛围数据：台词+状态+心情。"""
    say: str = ""
    status: str = ""
    mood: str = ""
    observation: str = ""
