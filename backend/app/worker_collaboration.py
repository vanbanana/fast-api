"""同事一对一协作流。

找人 -> 扑空改去对方当前位置 -> 当面沟通 -> 对方挂起一句回应。
状态全部由 WorkerStateMachine 承载，worker_agent 只负责编排。
"""
import random
from typing import TYPE_CHECKING

from app.domain import OfficeTargets
from app.llm_client import llm_client
from app.schemas import AgentCommand, WorkerEvent
from app.worker_llm_decision import clean_visible_text, safe_confidence, text_value
from app.worker_state import PendingReply, WorkerState

if TYPE_CHECKING:
    from app.worker_agent import OfficeAgent


async def continue_find_person_flow(
    agent: "OfficeAgent",
    event: WorkerEvent,
    targets: OfficeTargets,
    agents: dict[str, "OfficeAgent"],
) -> AgentCommand | None:
    """正在找人时的推进：扑空一次允许追到对方当前位置，找到则当面沟通。"""
    fsm = agent.fsm
    if fsm.state != WorkerState.SEEKING_COLLEAGUE or event.type != "worker_arrived":
        return None

    helper = agents.get(fsm.helper_id)
    helper_desk = targets.own_desk(fsm.helper_id)
    if helper is None or helper_desk is None:
        fsm.transition(WorkerState.IDLE)
        return None

    arrived_target = event.target_id or ""
    helper_last_target = helper.last_target_id or helper_desk
    helper_is_at_desk = helper_last_target == helper_desk

    if arrived_target == helper_desk and not helper_is_at_desk and not fsm.checked_helper_desk:
        fsm.checked_helper_desk = True
        target_id = helper_last_target if helper_last_target in targets.all_targets() else helper_desk
        context = {
            "intent": f"{helper.name} 不在工位，改去他当前所在位置找人",
            "work_update": f"去 {target_id} 找 {helper.name} 当面沟通",
            "risk_note": "",
            "needs_help_from": fsm.helper_id,
            "confirmation_question": "",
            "memory_note": f"{helper.name} 工位没人，转去 {target_id} 找他",
            "confidence": 0.86,
            "stream_lines": [
                f"{helper.name} 工位没人。",
                "我去他现在所在的位置找他。",
            ],
        }
        agent.remember(f"工作记忆:{helper.name} 工位没人，转去 {target_id} 找他")
        return agent.move_command(target_id, f"{helper.name} 不在工位，我去他那边找。", context, "visit")

    if arrived_target == helper_last_target or (arrived_target == helper_desk and helper_is_at_desk):
        context = await build_collaboration_context(agent, helper)
        fsm.transition(WorkerState.COLLABORATING)
        agent.needs_help_from = ""
        agent.status = f"和{helper.name}沟通"
        helper.status = f"回应{agent.name}的协作"
        helper.fsm.pending_reply = PendingReply(
            from_worker_id=agent.worker_id,
            say=text_value(context.get("helper_say", "")),
            context=dict(context),
        )
        helper.remember(f"协作回应:{agent.name}:{text_value(context.get('confirmation_question', '')) or text_value(context.get('work_update', ''))}")
        agent.remember(f"协作沟通:{helper.name}:{text_value(context.get('work_update', ''))}")
        return agent.say_command(text_value(context.get("say", "")), context)

    # 追了一轮还没遇上（对方持续移动），放弃追逐回到正常决策，避免无限绕圈
    if fsm.checked_helper_desk:
        helper_name = helper.name
        fsm.transition(WorkerState.IDLE)
        agent.needs_help_from = ""
        agent.remember(f"工作记忆:没遇到 {helper_name}，先回去推进自己的部分")
        return None
    return None


async def build_collaboration_context(agent: "OfficeAgent", helper: "OfficeAgent") -> dict[str, object]:
    """当面沟通的内容：优先走 LLM，失败时按岗位给确定性兜底。"""
    question = agent.confirmation_question or f"{helper.name}，我需要你帮我确认一下「{agent.focus_task}」这块。"
    try:
        data = await llm_client.complete_colleague_reply(
            requester_name=agent.name,
            requester_role=agent.role,
            helper_name=helper.name,
            helper_role=helper.role,
            helper_prompt=helper.roleplay_prompt(),
            question=question,
            task_title=agent.focus_task,
            risk_note=agent.current_risk,
        )
    except Exception:
        data = {}
    if data.get("reply") and data.get("work_update"):
        reply = clean_visible_text(data.get("reply", ""))
        next_step = clean_visible_text(data.get("next_step", "")) or clean_visible_text(data.get("work_update", ""))
        return {
            "intent": f"当面找 {helper.name} 处理协作问题",
            "work_update": clean_visible_text(data.get("work_update", "")),
            "risk_note": clean_visible_text(data.get("risk_note", "")) or agent.current_risk,
            "needs_help_from": "",
            "confirmation_question": question,
            "confidence": safe_confidence(data.get("confidence", 0.0)),
            "behavior_state": "collaboration_loop",
            "stream_lines": [
                f"已找到 {helper.name}。",
                f"{helper.name}回应：{reply}",
                next_step,
            ],
            "say": random.choice([
                "明白，我按这个去推。",
                "行，那我先这么改。",
                "好，有问题我再来找你。",
            ]),
            "helper_say": reply,
        }

    if "测试" in helper.role:
        update = f"请 {helper.name} 补验收点和回归风险"
    elif "产品" in helper.role:
        update = f"请 {helper.name} 明确用户场景和验收口径"
    elif "后端" in helper.role or "架构" in helper.role:
        update = f"请 {helper.name} 确认接口边界和技术风险"
    elif "前端" in helper.role or "UI" in helper.role:
        update = f"请 {helper.name} 确认页面状态和交互细节"
    else:
        update = f"请 {helper.name} 补充他负责范围的结论"
    return {
        "intent": f"当面找 {helper.name} 处理协作问题",
        "work_update": update,
        "risk_note": agent.current_risk,
        "needs_help_from": "",
        "confirmation_question": question,
        "confidence": 0.84,
        "behavior_state": "collaboration_loop",
        "stream_lines": [
            f"已找到 {helper.name}。",
            update,
        ],
        "say": question,
        "helper_say": random.choice([
            "我先看这块，回头给你结论。",
            "这个我了解，下午给你答复。",
            "行，这块我接了，你先忙别的。",
        ]),
    }


def consume_pending_reply(agent: "OfficeAgent") -> AgentCommand | None:
    """被请教方在下一次决策时优先把挂起的协作回应说出来。"""
    pending = agent.fsm.pending_reply
    if pending.is_empty():
        return None
    context = dict(pending.context)
    context["behavior_state"] = "collaboration_reply_loop"
    say = pending.say
    requester_id = pending.from_worker_id
    agent.fsm.pending_reply = PendingReply()
    agent.status = "回应同事协作"
    agent.remember(f"协作发言:{requester_id}:{say}")
    return agent.say_command(say, context)
