from typing import Protocol

from app.domain import ProjectTask


class WorkerRuleLike(Protocol):
    worker_id: str
    name: str
    role: str
    current_directive: str
    active_task_id: str
    focus_task: str
    energy: float
    stress: float


def build_rule_work_context(worker: WorkerRuleLike, target_id: str, active_task: ProjectTask | None) -> dict[str, object]:
    task_title = active_task.title if active_task else worker.focus_task
    context: dict[str, object] = {
        "intent": "按当前职责推进最需要处理的工作",
        "work_update": f"准备处理「{task_title}」",
        "risk_note": "",
        "needs_help_from": "",
        "confirmation_question": "",
        "memory_note": f"决定去 {target_id} 处理 {task_title}",
        "confidence": 0.68,
        "say": f"{worker.name}：我先处理「{task_title}」。",
    }
    if not worker.current_directive and active_task is None:
        context.update({
            "intent": "没有明确任务，先在办公室自然走动",
            "work_update": "空闲观察办公室状态",
            "memory_note": f"空闲移动到 {target_id}",
            "say": "",
            "confidence": 0.55,
        })
        return context

    if "Chair" in target_id:
        context.update({
            "intent": "通过会议同步上下文和风险",
            "work_update": f"准备在会议里同步「{task_title}」的状态",
            "memory_note": "",
            "say": "",
            "confidence": 0.74,
        })
    elif target_id.endswith("Marker"):
        context.update({
            "intent": "回到工位专注推进任务",
            "work_update": f"回工位推进「{task_title}」",
            "say": desk_work_line(worker, task_title),
            "confidence": 0.78,
        })
    elif worker.energy < 0.35 or worker.stress > 0.75:
        context.update({
            "intent": "短暂休息，避免压力影响判断",
            "work_update": "暂停推进，恢复精力后继续",
            "risk_note": "状态偏低，继续硬推可能降低质量",
            "say": f"{worker.name}：我缓一下，避免把问题越改越乱。",
            "confidence": 0.62,
        })

    helper = suggest_helper(worker, active_task)
    if helper:
        context["needs_help_from"] = helper
        context["work_update"] = f"{context['work_update']}，可能需要 {helper} 协作"
    if should_confirm_with_team(active_task):
        context["confirmation_question"] = build_confirmation_question(active_task)
        context["risk_note"] = context["risk_note"] or "团队内部需要先补齐验收口径"
    return context


def desk_work_line(worker: WorkerRuleLike, task_title: str) -> str:
    if "后端" in worker.role or "架构" in worker.role:
        return f"{worker.name}：我先看边界和日志，再动「{task_title}」。"
    if "测试" in worker.role:
        return f"{worker.name}：我会把复现步骤和回归点补齐。"
    if "产品" in worker.role:
        return f"{worker.name}：我先把验收标准写清楚。"
    if "UI" in worker.role or "前端" in worker.role:
        return f"{worker.name}：我先确认状态和交互细节。"
    return f"{worker.name}：我回工位推进「{task_title}」。"


def suggest_helper(worker: WorkerRuleLike, active_task: ProjectTask | None) -> str:
    if active_task is None:
        return ""
    if active_task.task_type == "backend" and worker.worker_id != "worker2":
        return "worker2"
    if active_task.task_type == "qa" and worker.worker_id != "worker6":
        return "worker6"
    if active_task.task_type == "design" and worker.worker_id != "worker5":
        return "worker5"
    if active_task.task_type == "product" and worker.worker_id != "worker3":
        return "worker3"
    return ""


def should_confirm_with_team(active_task: ProjectTask | None) -> bool:
    if active_task is None:
        return False
    unclear_words = ["尽快", "优化", "改好", "完善", "真实", "做一下"]
    return active_task.priority >= 4 and any(word in active_task.title for word in unclear_words)


def build_confirmation_question(active_task: ProjectTask | None) -> str:
    if active_task is None:
        return ""
    if active_task.task_type in ["backend", "qa", "design"]:
        return "项目经理拉产品、测试和对应负责人把验收标准补齐。"
    if active_task.task_type == "product":
        return "产品经理先补全用户场景和验收口径，再交给项目经理排期。"
    return "项目经理确认负责人、优先级和验收口径。"
