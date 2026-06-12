import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autogen_agentchat.base import ChatAgent, Response
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage, TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken

from app.llm_client import llm_client


TASK_TYPES = {"product", "backend", "frontend", "design", "qa", "data", "ops", "general"}


@dataclass(frozen=True)
class TeamParticipant:
    """项目规划参与者画像，来自真实员工配置。"""

    worker_id: str
    name: str
    role: str
    prompt: str


class OfficePlanningAgent(ChatAgent):
    """AutoGen ChatAgent 适配器：让各岗位在共享目标下拆分工作项。"""

    def __init__(self, participant: TeamParticipant, objective: str, all_participants: list[TeamParticipant]) -> None:
        self._participant = participant
        self._objective = objective
        self._all_participants = all_participants

    @property
    def name(self) -> str:
        return self._participant.worker_id

    @property
    def description(self) -> str:
        return f"{self._participant.name} / {self._participant.role}"

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken) -> Response:
        transcript = [
            {
                "speaker": self._speaker_name(message.source),
                "text": str(message.content),
            }
            for message in messages
            if isinstance(message, TextMessage) and message.source != "user"
        ]
        try:
            data = await llm_client.complete_team_planning_reply(
                objective=self._objective,
                speaker_name=self._participant.name,
                speaker_role=self._participant.role,
                speaker_prompt=self._participant.prompt,
                transcript=transcript,
            )
        except Exception:
            data = {}
        if not data.get("task_title"):
            data = self._fallback_plan_item()
        return Response(chat_message=TextMessage(source=self.name, content=json.dumps(data, ensure_ascii=False)))

    async def on_messages_stream(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        response = await self.on_messages(messages, cancellation_token)
        yield response

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None

    async def on_pause(self, cancellation_token: CancellationToken) -> None:
        return None

    async def on_resume(self, cancellation_token: CancellationToken) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save_state(self) -> Mapping[str, Any]:
        return {}

    async def load_state(self, state: Mapping[str, Any]) -> None:
        return None

    def _speaker_name(self, worker_id: str) -> str:
        for participant in self._all_participants:
            if participant.worker_id == worker_id:
                return participant.name
        return worker_id

    def _fallback_plan_item(self) -> dict[str, str]:
        role = self._participant.role
        objective = _objective_label(self._objective)
        if "项目经理" in role:
            return _plan("拆解范围、负责人和排期", "ops", self._participant.worker_id, f"拆解「{objective}」范围、负责人和排期")
        if "产品" in role:
            return _plan("补全用户场景和验收标准", "product", self._participant.worker_id, f"明确「{objective}」目标用户、场景和验收指标")
        if "架构" in role:
            return _plan("评估系统边界和技术方案", "backend", self._participant.worker_id, f"评估「{objective}」系统边界、模块拆分和技术风险")
        if "后端" in role:
            return _plan("评估接口、数据结构和服务边界", "backend", self._participant.worker_id, f"确认「{objective}」涉及的接口、权限和数据风险。")
        if "前端" in role:
            return _plan("实现页面状态和交互流程", "frontend", self._participant.worker_id, f"实现「{objective}」页面状态和交互流程")
        if "UI" in role or "设计" in role:
            return _plan("输出关键页面和异常状态设计", "design", self._participant.worker_id, f"输出「{objective}」关键页面和异常状态设计")
        if "测试" in role:
            return _plan("制定验收用例和回归范围", "qa", self._participant.worker_id, f"制定「{objective}」验收用例和回归范围")
        if "数据" in role:
            return _plan("定义指标和埋点口径", "data", self._participant.worker_id, f"定义「{objective}」指标和埋点口径")
        return _plan("补充跨团队协作事项", "general", self._participant.worker_id, f"同步「{objective}」相关依赖")


async def run_project_planning(
    *,
    objective: str,
    participants: list[TeamParticipant],
    max_turns: int,
) -> list[dict[str, str]]:
    """用 AutoGen RoundRobinGroupChat 生成项目任务拆解。"""
    if not participants:
        return []
    agents = [OfficePlanningAgent(participant, objective, participants) for participant in participants]
    team = RoundRobinGroupChat(agents, max_turns=max_turns)
    result = await team.run(task=_planning_task(objective, participants))

    items: list[dict[str, str]] = []
    participant_ids = {participant.worker_id for participant in participants}
    for message in result.messages:
        if not isinstance(message, TextMessage) or message.source not in participant_ids:
            continue
        item = _parse_plan_item(str(message.content))
        if not item:
            continue
        item["worker_id"] = message.source
        items.append(item)
    return _dedupe_plan_items(items)


def _planning_task(objective: str, participants: list[TeamParticipant]) -> str:
    roster = "、".join(f"{item.name}/{item.role}" for item in participants)
    return (
        f"老板目标：{objective}\n"
        f"参与规划：{roster}\n"
        "请每个岗位给出自己负责的一项可执行工作。"
        "输出必须是 JSON，不要闲聊。"
    )


def _parse_plan_item(text: str) -> dict[str, str]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    task_type = str(raw.get("task_type", "general")).strip()
    if task_type not in TASK_TYPES:
        task_type = "general"
    return {
        "contribution": str(raw.get("contribution", "")).strip()[:140],
        "task_title": str(raw.get("task_title", "")).strip()[:120],
        "task_type": task_type,
        "assignee_hint": str(raw.get("assignee_hint", "")).strip()[:30],
        "risk_note": str(raw.get("risk_note", "")).strip()[:120],
    }


def _dedupe_plan_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.get("worker_id", ""), item.get("task_title", ""))
        if not key[1] or key in seen:
            continue
        deduped.append(item)
        seen.add(key)
    return deduped


def _plan(contribution: str, task_type: str, assignee_hint: str, task_title: str) -> dict[str, str]:
    return {
        "contribution": contribution,
        "task_title": task_title,
        "task_type": task_type,
        "assignee_hint": assignee_hint,
        "risk_note": "",
    }


def _objective_label(objective: str) -> str:
    cleaned = objective.strip(" ：，。")
    for prefix in ["做一个", "做下", "做一下", "实现一个", "开发一个"]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.strip(" ：，。") or "当前目标"
