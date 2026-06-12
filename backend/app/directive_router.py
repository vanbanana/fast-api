from dataclasses import dataclass

from app.domain import BossDirective
from app.llm_client import llm_client


ROUTE_MEETING = "meeting"
ROUTE_WORK = "work"


@dataclass(frozen=True)
class DirectiveRoute:
    """老板指令的结构化路由结果。"""

    route: str
    confidence: float
    reason: str
    source: str

    @property
    def is_meeting(self) -> bool:
        return self.route == ROUTE_MEETING


async def route_directive(directive: BossDirective) -> DirectiveRoute:
    """优先使用 LLM function calling，失败时退回保守规则。"""
    try:
        data = await llm_client.complete_directive_route(directive.text)
    except Exception:
        data = {}
    route = str(data.get("route", "")).strip()
    confidence = _safe_confidence(data.get("confidence", 0.0))
    reason = str(data.get("reason", "")).strip()
    if route in [ROUTE_MEETING, ROUTE_WORK] and confidence >= 0.55:
        return DirectiveRoute(route, confidence, reason or "LLM 工具路由", "llm_tool")
    fallback = fallback_route_directive(directive)
    if reason:
        return DirectiveRoute(fallback.route, fallback.confidence, f"{fallback.reason}；LLM低置信:{reason}", fallback.source)
    return fallback


def fallback_route_directive(directive: BossDirective) -> DirectiveRoute:
    """离线兜底：只把明确会议行为路由到会议室。"""
    text = directive.text.lower()
    explicit_meeting_words = ["去会议室", "会议室", "开会", "召开会议", "进会议", "meeting room"]
    if any(word in text for word in explicit_meeting_words):
        return DirectiveRoute(ROUTE_MEETING, 0.7, "明确要求进入会议室或开会", "fallback_rule")
    return DirectiveRoute(ROUTE_WORK, 0.65, "默认作为工作目标拆解推进", "fallback_rule")


def _safe_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
