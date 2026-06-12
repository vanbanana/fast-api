"""LLM 员工决策的提示词组装和输出清洗。

LLM 只产出 ActionIntent（movement_type），不再直接选场景坐标；
所有可见文本统一清洗，防止 function calling 残片泄露给玩家。
"""
from typing import Protocol

from app.domain import CompanyProject, ProjectTask
from app.memory import memory_store
from app.prompt_library import render
from app.schemas import WorkerEvent
from app.worker_intent import WorkerDecision, parse_intent
from app.worker_rule_context import build_confirmation_question


class WorkerLLMLike(Protocol):
    worker_id: str
    name: str
    role: str
    status: str
    mood: str
    energy: float
    stress: float
    focus_task: str
    current_directive: str
    memory: list[str]

    def roleplay_prompt(self) -> str:
        ...


def build_agent_decision_messages(
    worker: WorkerLLMLike,
    event: WorkerEvent,
    company: CompanyProject,
    active_task: ProjectTask | None,
    colleagues: list[str],
) -> tuple[str, str]:
    task_text = active_task.snapshot().model_dump() if active_task else "无"
    system = render("agent_decision_system.md")
    user = render(
        "agent_decision_user.md",
        roleplay_prompt=worker.roleplay_prompt(),
        memory_context=memory_store.build_context(worker.worker_id, worker.focus_task + worker.current_directive + worker.role),
        status=worker.status,
        mood=worker.mood,
        energy=worker.energy,
        stress=worker.stress,
        focus_task=worker.focus_task,
        task_text=task_text,
        directive=worker.current_directive or "无",
        morale=company.morale,
        release_risk=company.release_risk,
        colleagues=colleagues,
        event_text=event.model_dump(),
        recent_memory=worker.memory[-10:],
    )
    return system, user


def decision_from_llm_data(data: dict[str, object], active_task: ProjectTask | None) -> WorkerDecision:
    """把工具调用原始参数转成结构化 WorkerDecision，并清洗可见文本。"""
    cleaned = normalize_llm_decision(data, active_task)
    return WorkerDecision(
        intent=parse_intent(cleaned.get("movement_type")),
        helper_id=text_value(cleaned.get("colleague_id", "")).strip(),
        say=text_value(cleaned.get("say", ""))[:60],
        status=text_value(cleaned.get("status", ""))[:30],
        mood=text_value(cleaned.get("mood", ""))[:20],
        focus_task=text_value(cleaned.get("focus_task", ""))[:60],
        intent_text=text_value(cleaned.get("intent", "")),
        work_update=text_value(cleaned.get("work_update", "")),
        risk_note=text_value(cleaned.get("risk_note", ""))[:100],
        needs_help_from=text_value(cleaned.get("needs_help_from", ""))[:20],
        confirmation_question=text_value(cleaned.get("confirmation_question", ""))[:100],
        memory_note=text_value(cleaned.get("memory_note", "")),
        confidence=safe_confidence(cleaned.get("confidence", 0.0)),
        stream_lines=list(cleaned.get("stream_lines", [])),
        source="llm",
    )


def normalize_llm_decision(data: dict[str, object], active_task: ProjectTask | None) -> dict[str, object]:
    """清洗 function calling 偶发的工具参数残片，避免玩家看到后台格式。"""
    cleaned: dict[str, object] = dict(data)
    text_keys = [
        "movement_type",
        "colleague_id",
        "say",
        "status",
        "mood",
        "focus_task",
        "intent",
        "work_update",
        "risk_note",
        "needs_help_from",
        "confirmation_question",
        "memory_note",
    ]
    for key in text_keys:
        cleaned[key] = clean_visible_text(cleaned.get(key, ""))

    question = text_value(cleaned.get("confirmation_question", ""))
    if looks_like_tool_noise(question):
        cleaned["confirmation_question"] = build_confirmation_question(active_task)

    raw_lines = cleaned.get("stream_lines", [])
    stream_lines: list[str] = []
    if isinstance(raw_lines, list):
        for item in raw_lines:
            line = clean_visible_text(item)
            if line and not looks_like_tool_noise(line):
                stream_lines.append(line[:90])
    cleaned["stream_lines"] = stream_lines[:5]
    return cleaned


def clean_visible_text(value: object) -> str:
    text = text_value(value).strip()
    for marker in ["<parameter=", "</parameter", "<tool_call", "</tool_call", "```"]:
        index = text.find(marker)
        if index >= 0:
            text = text[:index].strip()
    while "，，" in text:
        text = text.replace("，，", "，")
    while "。。" in text:
        text = text.replace("。。", "。")
    return text[:140]


def looks_like_tool_noise(text: str) -> bool:
    if not text:
        return False
    return "<parameter=" in text or "</" in text or text.count("，") > 12 or text.count(".") > 12


def agent_stream_lines(context: dict[str, object], say: str) -> list[str]:
    raw_lines = context.get("stream_lines", [])
    lines: list[str] = []
    if isinstance(raw_lines, list):
        for item in raw_lines:
            text = str(item).strip()
            if text:
                lines.append(text[:90])
    if not lines:
        lines = [
            f"判断: {text_value(context.get('intent', '先确认手头工作'))[:80]}",
            f"推进: {text_value(context.get('work_update', '更新任务状态'))[:80]}",
        ]
    risk = text_value(context.get("risk_note", "")).strip()
    helper = text_value(context.get("needs_help_from", "")).strip()
    question = text_value(context.get("confirmation_question", "")).strip()
    if risk:
        lines.append(f"风险: {risk[:80]}")
    if helper:
        lines.append(f"协作: 准备找 {helper}")
    if question:
        lines.append(f"待确认: {question[:80]}")
    if say:
        lines.append(f"台词: {say[:80]}")
    return lines[-8:]


def text_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def safe_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
