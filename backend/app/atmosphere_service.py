"""氛围服务：后端唯一对外暴露的 LLM 接口。

只负责根据员工当前状态生成一句台词、一句状态描述和一个心情。
不决定移动、不管理 FSM、不推进任务——这些全由 Godot 本地状态机处理。
"""
import random
import time
from dataclasses import dataclass

from app.llm_client import llm_client


@dataclass
class AtmosphereRequest:
    worker_id: str
    name: str
    role: str
    personality: str
    state: str              # Godot 本地状态: idle/working/break/roaming
    location: str           # 当前 Marker 名称
    nearby_workers: list[str]  # 附近同事名字列表
    last_event: str         # 刚发生的事: arrived/idle/chat 等
    current_task: str = ""  # 当前任务标题（可选）
    energy: float = 1.0
    stress: float = 0.0


@dataclass
class AtmosphereResponse:
    say: str = ""           # 台词（<40字日常口语）
    status: str = ""        # 状态气泡文本（<20字）
    mood: str = ""          # 心情
    observation: str = ""   # 可选内心独白


# ====== 缓存：同 worker N 秒内不重复调 LLM ======
_cache: dict[str, tuple[float, AtmosphereResponse]] = {}
_CACHE_TTL = 8.0  # 秒


async def generate(request: AtmosphereRequest) -> AtmosphereResponse:
    """主入口：带缓存 + LLM 降级。"""
    # 1. 缓存命中
    cached = _cache.get(request.worker_id)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
        return cached[1]

    # 2. 尝试 LLM
    response = await _call_llm(request)
    if response.say:  # LLM 返回了有效内容
        _cache[request.worker_id] = (time.monotonic(), response)
        return response

    # 3. 降级：规则模板
    response = _fallback_response(request)
    _cache[request.worker_id] = (time.monotonic(), response)
    return response


def _build_system_prompt() -> str:
    """系统提示词：角色定义 + 记忆使用规则。"""
    try:
        with open("prompts/system/agent_decision_system.md", encoding="utf-8") as f:
            base = f.read()
    except Exception:
        base = ""
    return base


def _build_user_prompt(req: AtmosphereRequest) -> str:
    """用户提示词：精简的当前上下文 ~300 字。"""
    lines = [
        f"你是{req.name}，{req.role}。{req.personality}",
        f"当前状态：{req.state}，位置：{req.location}。",
    ]
    if req.current_task:
        lines.append(f"手头任务：{req.current_task}")
    if req.nearby_workers:
        names = "、".join(req.nearby_workers[:5])
        lines.append(f"附近同事：{names}")
    if req.last_event and req.last_event != "none":
        lines.append(f"刚发生：{_event_description(req.last_event)}")
    lines.append("")
    lines.append("请输出一句符合你人设的短台词（日常口语，像随口说的，不超过30字）和你此刻的工作状态描述（不超过15字）。只输出 JSON 格式：{\"say\": \"...\", \"status\": \"...\", \"mood\": \"...\"}")
    return "\n".join(lines)


def _event_description(event: str) -> str:
    mapping = {
        "arrived": "刚走到当前位置",
        "idle": "原地待了一会儿",
        "chat": "跟同事聊了两句",
        "task_done": "刚完成一项任务",
        "boss_directive": "老板刚下达了新指令",
        "meeting_start": "会议要开始了",
        "meeting_end": "会议结束了",
    }
    return mapping.get(event, event)


async def _call_llm(req: AtmosphereRequest) -> AtmosphereResponse:
    """调用 LLM 生成氛围内容。失败返回空响应触发降级。"""
    system = _build_system_prompt()
    user = _build_user_prompt(req)
    try:
        data = await llm_client.complete_json(system, user)
        if not data:
            return AtmosphereResponse()
        return _parse_llm_output(data, req.state)
    except Exception:
        return AtmosphereResponse()


def _parse_llm_output(data: dict, state: str) -> AtmosphereResponse:
    """从 LLM 返回值中提取 say/status/mood。"""
    raw_text = str(data.get("content", ""))
    say = str(data.get("say", ""))
    status = str(data.get("status", ""))
    mood = str(data.get("mood", ""))

    # 如果 LLM 返回的是纯文本而非结构化 JSON，尝试从文本提取
    if not say and raw_text:
        say = raw_text.strip()[:50]

    # 截断过长内容
    if len(say) > 60:
        say = say[:57] + "..."
    if len(status) > 25:
        status = status[:22] + "..."
    if len(mood) > 10:
        mood = mood[:7] + "..."

    return AtmosphereResponse(
        say=say,
        status=status or _default_status_for_state(state),
        mood=mood or "平稳",
    )


def _default_status_for_state(state: str) -> str:
    mapping = {
        "idle": "在办公室待命",
        "working": "在工位推进工作",
        "break": "短暂休息",
        "roaming": "在办公室走动",
        "seeking": "在找人",
        "chatting": "和同事交谈",
    }
    return mapping.get(state, "在办公室")


def _fallback_response(req: AtmosphereRequest) -> AtmosphereResponse:
    """LLM 不可用时从模板池选取默认回复。"""
    templates = _FALLBACK_TEMPLATES.get(req.state, _FALLBACK_TEMPLATES["working"])
    tpl = random.choice(templates)
    return AtmosphereResponse(
        say=tpl["say"],
        status=tpl["status"],
        mood=tpl["mood"],
    )


_FALLBACK_TEMPLATES = {
    "working": [
        {"say": "这块我再过一遍。", "status": "在工位推进任务", "mood": "专注"},
        {"say": "接口文档我再看一遍边界情况。", "status": "在工位整理文档", "mood": "投入"},
        {"say": "这个逻辑有点绕，我得理一下。", "status": "在工位思考问题", "mood": "略微分心"},
        {"say": "先把这个改完再说。", "status": "在工位写代码", "mood": "专注"},
        {"say": "跑一遍看看有没有遗漏。", "status": "在工位自测", "mood": "平稳"},
    ],
    "idle": [
        {"say": "等下个任务分配过来。", "status": "在工位待命", "mood": "平稳"},
        {"say": "先整理一下今天的待办。", "status": "在工位整理", "mood": "投入"},
    ],
    "break": [
        {"say": "缓会儿再回去。", "status": "短暂休息", "mood": "放松"},
        {"say": "喝口水，脑子清醒点。", "status": "在休息区", "mood": "轻松"},
        {"say": "站一会儿，腰快断了。", "status": "活动身体", "mood": "疲惫但放松"},
    ],
    "roaming": [
        {"say": "转一圈看看大家都在干嘛。", "status": "在办公室走动", "mood": "随意"},
        {"say": "活动活动，坐太久了。", "status": "走动休息", "mood": "轻松"},
    ],
    "seeking": [
        {"say": "看到了，过去一下。", "status": "在找人", "mood": "期待"},
        {"say": "应该就在附近。", "status": "在找人路上", "mood": "随意"},
    ],
    "chatting": [
        {"say": "嗯，聊两句。", "status": "和同事交谈", "mood": "轻松"},
        {"say": "你说得对，回头再说。", "status": "在聊天", "mood": "愉快"},
    ],
}


def invalidate_cache(worker_id: str | None = None) -> None:
    """清除缓存，用于 boss command 后强制刷新。"""
    if worker_id:
        _cache.pop(worker_id, None)
    else:
        _cache.clear()
