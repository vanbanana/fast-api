"""员工运行态状态机。

把原本散落在 OfficeAgent 上的零散布尔/字符串运行态字段收敛成一个
显式 FSM，所有状态切换都必须经过 transition()，非法迁移会被拒绝并
回退到安全状态，避免"会议中途跑去找同事"这类行为漂移。
"""
from dataclasses import dataclass, field
from enum import Enum


class WorkerState(str, Enum):
    IDLE = "idle"                     # 没有任务，自由观察/闲逛
    WORKING = "working"               # 在自己工位推进任务
    ROAMING = "roaming"               # 空闲走动
    RESTING = "resting"               # 在休息点恢复精力
    SEEKING_COLLEAGUE = "seeking"     # 正在去找同事的路上
    COLLABORATING = "collaborating"   # 和同事当面沟通中
    MEETING = "meeting"               # 被会议锁定（入座/发言/等待）
    COOLDOWN = "cooldown"             # 自主循环冷却，整理状态


# 不在工位的 FSM 状态：闲逛、休息、找同事。会议是老板指令驱动，不计入并发限制。
AWAY_FROM_DESK_STATES: frozenset[WorkerState] = frozenset({
    WorkerState.ROAMING,
    WorkerState.RESTING,
    WorkerState.SEEKING_COLLEAGUE,
})


_ALLOWED: dict[WorkerState, frozenset[WorkerState]] = {
    WorkerState.IDLE: frozenset({
        WorkerState.WORKING, WorkerState.ROAMING, WorkerState.RESTING,
        WorkerState.SEEKING_COLLEAGUE, WorkerState.MEETING, WorkerState.COOLDOWN,
    }),
    WorkerState.WORKING: frozenset({
        WorkerState.IDLE, WorkerState.ROAMING, WorkerState.RESTING,
        WorkerState.SEEKING_COLLEAGUE, WorkerState.MEETING, WorkerState.COOLDOWN,
    }),
    WorkerState.ROAMING: frozenset({
        WorkerState.IDLE, WorkerState.WORKING, WorkerState.RESTING,
        WorkerState.SEEKING_COLLEAGUE, WorkerState.MEETING, WorkerState.COOLDOWN,
    }),
    WorkerState.RESTING: frozenset({
        WorkerState.IDLE, WorkerState.WORKING, WorkerState.ROAMING,
        WorkerState.MEETING, WorkerState.COOLDOWN,
    }),
    WorkerState.SEEKING_COLLEAGUE: frozenset({
        WorkerState.COLLABORATING, WorkerState.WORKING, WorkerState.IDLE,
        WorkerState.MEETING,
    }),
    WorkerState.COLLABORATING: frozenset({
        WorkerState.WORKING, WorkerState.IDLE, WorkerState.MEETING,
    }),
    WorkerState.MEETING: frozenset({
        WorkerState.WORKING, WorkerState.IDLE,
    }),
    WorkerState.COOLDOWN: frozenset({
        WorkerState.IDLE, WorkerState.WORKING, WorkerState.ROAMING,
    }),
}


@dataclass
class PendingReply:
    """同事协作时挂起的一句回应，由被请教方在下一次决策时消费。"""

    from_worker_id: str = ""
    say: str = ""
    context: dict[str, object] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.say


@dataclass
class WorkerStateMachine:
    state: WorkerState = WorkerState.IDLE
    helper_id: str = ""               # SEEKING_COLLEAGUE 时要找的同事
    checked_helper_desk: bool = False  # 是否已经扑空过对方工位一次
    pending_reply: PendingReply = field(default_factory=PendingReply)

    def can_transition(self, target: WorkerState) -> bool:
        if target == self.state:
            return True
        return target in _ALLOWED[self.state]

    def transition(self, target: WorkerState) -> bool:
        """尝试切换状态；非法迁移返回 False 且保持原状态。"""
        if not self.can_transition(target):
            return False
        if target != self.state:
            self._on_exit(self.state)
        self.state = target
        return True

    def force(self, target: WorkerState) -> None:
        """会议调度等外部权威来源使用，跳过迁移表但仍执行清理。"""
        if target != self.state:
            self._on_exit(self.state)
        self.state = target

    def start_seeking(self, helper_id: str) -> bool:
        if not self.transition(WorkerState.SEEKING_COLLEAGUE):
            return False
        self.helper_id = helper_id
        self.checked_helper_desk = False
        return True

    def stop_seeking(self) -> None:
        self.helper_id = ""
        self.checked_helper_desk = False

    def reset(self) -> None:
        self.state = WorkerState.IDLE
        self.helper_id = ""
        self.checked_helper_desk = False
        self.pending_reply = PendingReply()

    def _on_exit(self, state: WorkerState) -> None:
        if state == WorkerState.SEEKING_COLLEAGUE:
            self.stop_seeking()
