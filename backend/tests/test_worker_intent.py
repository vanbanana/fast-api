import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain import OfficeTargets
from app.worker_intent import ActionIntent, WorkerDecision, apply_resolution, parse_intent, resolve_decision


@dataclass
class FakeWorker:
    worker_id: str
    name: str
    assigned_meeting_seat: str = ""


def _targets() -> OfficeTargets:
    return OfficeTargets(
        seats=["worker1Marker", "worker2Marker", "leftTopChair"],
        roam_points=["desk1"],
        idle_points=["sofa1"],
    )


def verify_intent_resolution() -> None:
    targets = _targets()
    worker = FakeWorker("worker1", "林主管")
    agents_ids = {"worker1", "worker2"}

    assert parse_intent("visit_colleague") == ActionIntent.VISIT_COLLEAGUE
    assert parse_intent("不认识的动作") == ActionIntent.WORK_AT_DESK

    # 找得到同事：解析为对方工位，意图保持，台词不被改写
    decision = WorkerDecision(intent=ActionIntent.VISIT_COLLEAGUE, helper_id="worker2", say="我去找小周确认接口。")
    resolved = resolve_decision(worker, decision, targets, agents_ids)
    assert resolved.target_id == "worker2Marker"
    assert resolved.travel_mode == "visit"
    assert not resolved.downgraded
    decision = apply_resolution(decision, resolved)
    assert decision.say == "我去找小周确认接口。"

    # 找不到同事：显式降级回工位，台词同步重写，保证言行一致
    decision = WorkerDecision(intent=ActionIntent.VISIT_COLLEAGUE, helper_id="ghost", say="我去找小王。", needs_help_from="ghost")
    resolved = resolve_decision(worker, decision, targets, agents_ids)
    assert resolved.downgraded
    assert resolved.target_id == "worker1Marker"
    decision = apply_resolution(decision, resolved)
    assert decision.intent == ActionIntent.WORK_AT_DESK
    assert decision.say != "我去找小王。"
    assert decision.intent_text == resolved.downgrade_reason
    assert decision.needs_help_from == ""

    # 没有会议座位却想去会议室：降级回工位
    decision = WorkerDecision(intent=ActionIntent.JOIN_MEETING, say="我去开会。")
    resolved = resolve_decision(worker, decision, targets, agents_ids)
    assert resolved.downgraded
    decision = apply_resolution(decision, resolved)
    assert decision.intent == ActionIntent.WORK_AT_DESK
    assert decision.say != "我去开会。"

    # 有会议座位：正常进会议室
    seated = FakeWorker("worker1", "林主管", assigned_meeting_seat="leftTopChair")
    decision = WorkerDecision(intent=ActionIntent.JOIN_MEETING)
    resolved = resolve_decision(seated, decision, targets, agents_ids)
    assert resolved.target_id == "leftTopChair"
    assert resolved.travel_mode == "meeting"

    # 不允许休息时想休息：降级回工位
    decision = WorkerDecision(intent=ActionIntent.TAKE_BREAK, say="我去摸鱼。")
    resolved = resolve_decision(worker, decision, targets, agents_ids, allow_break=False)
    assert resolved.downgraded
    decision = apply_resolution(decision, resolved)
    assert decision.say != "我去摸鱼。"

    # 允许休息：去休息点
    decision = WorkerDecision(intent=ActionIntent.TAKE_BREAK)
    resolved = resolve_decision(worker, decision, targets, agents_ids, allow_break=True)
    assert resolved.target_id == "sofa1"


if __name__ == "__main__":
    verify_intent_resolution()
    print("worker intent checks passed")
