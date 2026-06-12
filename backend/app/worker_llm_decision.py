from typing import Protocol

from app.config import settings
from app.domain import CompanyProject, OfficeTargets, ProjectTask
from app.memory import memory_store
from app.schemas import WorkerEvent
from app.worker_decision_policy import llm_work_targets
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
    autonomy_steps: int
    memory: list[str]

    def roleplay_prompt(self) -> str:
        ...


def build_agent_decision_messages(
    worker: WorkerLLMLike,
    event: WorkerEvent,
    targets: OfficeTargets,
    company: CompanyProject,
    active_task: ProjectTask | None,
) -> tuple[str, str]:
    task_text = active_task.snapshot().model_dump() if active_task else "无"
    system = (
        "你正在扮演一家小型软件公司的真实员工，不是通用机器人。"
        "你要像真实同事一样考虑岗位职责、性格、压力、任务上下文、同事关系和老板指令。"
        "玩家只输入一次初始业务目标，公司内部员工必须自行补全需求、拆解任务、确认验收和解决协作问题。"
        "技术、验收、接口、设计问题走项目经理、产品、架构师、测试或对应同事的内部协作链。"
        "你的输出会驱动游戏角色移动和公司任务状态，所以必须具体、短句、贴近办公室工作。"
        "必须调用 office_agent_decision 工具提交结果。"
        "movement_type 必须按真实办公室动作选择：正常工作用 own_desk；要找人用 visit_colleague；讨论评审用 meeting；只有疲劳、高压或明确摸鱼休息才用 break。"
        "stream_lines 是玩家能看到的角色内心/工作流短句，不要写模型推理链。"
        f"可选工作目标：{llm_work_targets(worker, targets)}。"
        f"休息目标只在明确休息、疲劳或摸鱼时使用：{targets.idle_points}。"
    )
    user = (
        f"{worker.roleplay_prompt()}"
        f"长期记忆和压缩上下文：{memory_store.build_context(worker.worker_id, worker.focus_task + worker.current_directive + worker.role)}。"
        f"当前状态：{worker.status}；心情：{worker.mood}；精力：{worker.energy:.2f}。"
        f"压力：{worker.stress:.2f}；当前任务：{worker.focus_task}；任务对象：{task_text}；老板指令：{worker.current_directive or '无'}。"
        f"公司士气：{company.morale:.2f}；发布风险：{company.release_risk:.2f}。"
        f"自主循环次数：{worker.autonomy_steps}/{settings.max_autonomy_steps}。"
        f"刚收到事件：{event.model_dump()}。最近记忆：{worker.memory[-10:]}。"
    )
    return system, user


def normalize_llm_decision(data: dict[str, object], active_task: ProjectTask | None) -> dict[str, object]:
    """清洗 function calling 偶发的工具参数残片，避免玩家看到后台格式。"""
    cleaned: dict[str, object] = dict(data)
    text_keys = [
        "target_id",
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
