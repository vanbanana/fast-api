import random
from dataclasses import dataclass
from typing import Protocol

from app.config import settings
from app.domain import CompanyProject, OfficeTargets, ProjectTask
from app.office_clock import office_clock


STATE_MEETING_ASSIGNED = "meeting_assigned"
STATE_FREE_ROAM = "free_roam"
STATE_BREAK = "break"
STATE_WORK_AT_DESK = "work_at_desk"
STATE_WAIT = "wait"


class WorkerBehaviorLike(Protocol):
    """行为树只读取必要黑板字段，避免依赖完整 OfficeAgent。"""

    worker_id: str
    current_directive: str
    active_task_id: str
    assigned_meeting_seat: str
    energy: float
    stress: float
    personality: str
    work_style: str


@dataclass(frozen=True)
class BehaviorDecision:
    """行为树输出：目标、移动模式和状态，供 OfficeAgent 组装命令。"""

    state: str
    target_id: str
    travel_mode: str
    active_task: ProjectTask | None = None


def choose_rule_behavior(
    worker: WorkerBehaviorLike,
    targets: OfficeTargets,
    company: CompanyProject,
    *,
    rng: random.Random | None = None,
) -> BehaviorDecision | None:
    """员工规则行为树。

    优先级：
    1. 已被会议系统分配座位，只能去会议座位。
    2. 无 boss 指令且无任务，自然闲逛，低概率休息。
    3. 精力/压力触发休息。
    4. 有任务或指令，回本人固定工位工作。
    """
    active_task = company.tasks.get(worker.active_task_id)
    if active_task and (active_task.status == "done" or active_task.assignee_id != worker.worker_id):
        active_task = None
    own_desk = targets.own_desk(worker.worker_id)
    rand = rng or random

    if worker.assigned_meeting_seat:
        return BehaviorDecision(STATE_MEETING_ASSIGNED, worker.assigned_meeting_seat, "meeting", active_task)

    has_work = bool(worker.current_directive or active_task)
    if not has_work:
        if targets.idle_points and rand.random() < settings.break_chance * office_clock.break_bias() * persona_break_bias(worker):
            return BehaviorDecision(STATE_BREAK, rand.choice(targets.idle_points), "roam", active_task)
        if targets.roam_points:
            return BehaviorDecision(STATE_FREE_ROAM, rand.choice(targets.roam_points), "roam", active_task)
        if own_desk:
            return BehaviorDecision(STATE_FREE_ROAM, own_desk, "roam", active_task)
        return None

    if should_take_break(worker, rng=rand) and targets.idle_points:
        return BehaviorDecision(STATE_BREAK, rand.choice(targets.idle_points), "normal", active_task)

    if own_desk:
        return BehaviorDecision(STATE_WORK_AT_DESK, own_desk, "normal", active_task)
    return BehaviorDecision(STATE_WAIT, "", "normal", active_task)


def should_take_break(worker: WorkerBehaviorLike, *, rng: random.Random | None = None) -> bool:
    """休息是员工状态驱动，不由会议或工作文本触发；叠加时段和人设偏置。"""
    if worker.energy <= settings.low_energy_rest_threshold:
        return True
    if worker.stress >= settings.high_stress_rest_threshold:
        return True
    rand = rng or random
    return rand.random() < settings.break_chance * office_clock.break_bias() * persona_break_bias(worker)


_DILIGENT_WORDS = ["严谨", "自律", "专注", "加班", "卷", "高效", "节奏快"]
_RELAXED_WORDS = ["随性", "摸鱼", "悠闲", "佛系", "慢热", "放松", "社交"]


def persona_break_bias(worker: WorkerBehaviorLike) -> float:
    """人设影响行为频率：严谨自律的人少休息，随性佛系的人多休息。"""
    text = f"{worker.personality}{worker.work_style}"
    bias = 1.0
    if any(word in text for word in _DILIGENT_WORDS):
        bias *= 0.6
    if any(word in text for word in _RELAXED_WORDS):
        bias *= 1.6
    return bias


def status_for_behavior_state(state: str, has_work: bool) -> str:
    if state == STATE_MEETING_ASSIGNED:
        return "去会议室"
    if state == STATE_BREAK:
        return "短暂休息"
    if state == STATE_WORK_AT_DESK:
        return "回到工位推进任务" if has_work else "在工位整理状态"
    if state == STATE_FREE_ROAM:
        return "在办公室闲逛"
    return "等待下一步"
