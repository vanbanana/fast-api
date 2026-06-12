"""统一的行动意图解析层。

LLM 和规则行为树都只产出 ActionIntent（做什么），由 resolve_decision()
统一映射为具体场景目标（去哪里）。如果意图不可执行（没有座位、找不到
同事等），意图会被显式降级，并且台词/意图文案一并重写，保证角色
"说的"和"做的"永远一致。
"""
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from app.domain import OfficeTargets


class ActionIntent(str, Enum):
    WORK_AT_DESK = "own_desk"          # 回自己工位干活
    VISIT_COLLEAGUE = "visit_colleague"  # 去同事工位当面沟通
    JOIN_MEETING = "meeting"           # 去会议室入座（只能由会议运行态分配座位）
    TAKE_BREAK = "break"               # 去休息点恢复精力
    ROAM = "roam"                      # 空闲走动
    STAY = "stay"                      # 原地整理，不移动


VALID_INTENTS = {item.value for item in ActionIntent}


class WorkerLike(Protocol):
    worker_id: str
    name: str
    assigned_meeting_seat: str


@dataclass
class ResolvedAction:
    """意图解析结果：具体目标 + travel_mode + 是否发生过降级。"""

    intent: ActionIntent
    target_id: str
    travel_mode: str = "normal"
    helper_id: str = ""
    downgraded: bool = False
    downgrade_reason: str = ""


@dataclass
class WorkerDecision:
    """一次决策的完整产出，替代原先到处传的裸 dict。"""

    intent: ActionIntent = ActionIntent.STAY
    target_id: str = ""
    travel_mode: str = "normal"
    helper_id: str = ""
    say: str = ""
    status: str = ""
    mood: str = ""
    focus_task: str = ""
    intent_text: str = ""
    work_update: str = ""
    risk_note: str = ""
    needs_help_from: str = ""
    confirmation_question: str = ""
    memory_note: str = ""
    confidence: float = 0.0
    stream_lines: list[str] = field(default_factory=list)
    behavior_state: str = ""
    source: str = "rule"

    def work_context(self) -> dict[str, object]:
        return {
            "intent": self.intent_text,
            "work_update": self.work_update,
            "risk_note": self.risk_note,
            "needs_help_from": self.needs_help_from,
            "confirmation_question": self.confirmation_question,
            "confidence": self.confidence,
        }

    def as_context(self) -> dict[str, object]:
        context = dict(self.work_context())
        context["say"] = self.say
        context["memory_note"] = self.memory_note
        context["stream_lines"] = list(self.stream_lines)
        context["behavior_state"] = self.behavior_state
        return context


def parse_intent(raw: object) -> ActionIntent:
    text = str(raw or "").strip()
    if text in VALID_INTENTS:
        return ActionIntent(text)
    return ActionIntent.WORK_AT_DESK


def resolve_decision(
    worker: WorkerLike,
    decision: WorkerDecision,
    targets: OfficeTargets,
    agents_ids: set[str],
    *,
    allow_break: bool = True,
    rng: random.Random | None = None,
) -> ResolvedAction:
    """把意图映射为唯一安全目标；不可执行时显式降级。"""
    rng = rng or random
    own_desk = targets.own_desk(worker.worker_id)

    if decision.intent == ActionIntent.JOIN_MEETING:
        if worker.assigned_meeting_seat:
            return ResolvedAction(ActionIntent.JOIN_MEETING, worker.assigned_meeting_seat, "meeting")
        return downgrade_to_desk(worker, targets, "没有被分配会议座位，回工位继续推进")

    if decision.intent == ActionIntent.VISIT_COLLEAGUE:
        helper_id = decision.helper_id or decision.needs_help_from
        helper_desk = targets.own_desk(helper_id) if helper_id in agents_ids else None
        if helper_id and helper_id != worker.worker_id and helper_desk:
            return ResolvedAction(ActionIntent.VISIT_COLLEAGUE, helper_desk, "visit", helper_id=helper_id)
        return downgrade_to_desk(worker, targets, "找不到要拜访的同事，先回工位")

    if decision.intent == ActionIntent.TAKE_BREAK:
        if allow_break and targets.idle_points:
            return ResolvedAction(ActionIntent.TAKE_BREAK, rng.choice(targets.idle_points))
        return downgrade_to_desk(worker, targets, "现在不适合休息，回工位继续")

    if decision.intent == ActionIntent.ROAM:
        if targets.roam_points:
            return ResolvedAction(ActionIntent.ROAM, rng.choice(targets.roam_points), "roam")
        return downgrade_to_desk(worker, targets, "没有可走动的位置，回工位")

    if decision.intent == ActionIntent.STAY:
        return ResolvedAction(ActionIntent.STAY, "", "stay")

    # WORK_AT_DESK 以及一切兜底
    if own_desk:
        return ResolvedAction(ActionIntent.WORK_AT_DESK, own_desk)
    if targets.roam_points:
        return ResolvedAction(
            ActionIntent.ROAM, rng.choice(targets.roam_points), "roam",
            downgraded=True, downgrade_reason="没有本人工位，先在办公区走动",
        )
    return ResolvedAction(ActionIntent.STAY, "", "stay", downgraded=True, downgrade_reason="没有任何可用目标")


def apply_resolution(decision: WorkerDecision, resolved: ResolvedAction) -> WorkerDecision:
    """把解析结果写回决策。若发生降级，重写台词和意图文案保证言行一致。"""
    decision.target_id = resolved.target_id
    decision.travel_mode = resolved.travel_mode
    decision.helper_id = resolved.helper_id
    if resolved.downgraded or resolved.intent != decision.intent:
        decision.intent = resolved.intent
        decision.intent_text = resolved.downgrade_reason or _default_intent_text(resolved.intent)
        decision.say = ""
        decision.stream_lines = [decision.intent_text]
        if resolved.intent != ActionIntent.VISIT_COLLEAGUE:
            decision.needs_help_from = ""
    return decision


def downgrade_to_desk(worker: WorkerLike, targets: OfficeTargets, reason: str) -> ResolvedAction:
    own_desk = targets.own_desk(worker.worker_id)
    if own_desk:
        return ResolvedAction(ActionIntent.WORK_AT_DESK, own_desk, downgraded=True, downgrade_reason=reason)
    return ResolvedAction(ActionIntent.STAY, "", "stay", downgraded=True, downgrade_reason=reason)


def _default_intent_text(intent: ActionIntent) -> str:
    mapping = {
        ActionIntent.WORK_AT_DESK: "回本人工位推进当前任务",
        ActionIntent.VISIT_COLLEAGUE: "去同事工位当面沟通",
        ActionIntent.JOIN_MEETING: "去会议室入座",
        ActionIntent.TAKE_BREAK: "短暂休息恢复精力",
        ActionIntent.ROAM: "在办公室走动观察",
        ActionIntent.STAY: "原地整理工作状态",
    }
    return mapping[intent]
