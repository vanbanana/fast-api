from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autogen_agentchat.base import ChatAgent, Response
from autogen_agentchat.messages import BaseChatMessage, BaseAgentEvent, TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken

from app.llm_client import llm_client
from app.prompt_library import load_lines, render


@dataclass(frozen=True)
class MeetingParticipant:
    """会议参与者画像，来自本地员工 Markdown。"""

    worker_id: str
    name: str
    role: str
    prompt: str


class OfficeMeetingAgent(ChatAgent):
    """AutoGen ChatAgent 适配器：每个 Godot 员工对应一个真实 group-chat agent。"""

    def __init__(self, participant: MeetingParticipant, topic: str, all_participants: list[MeetingParticipant]) -> None:
        self._participant = participant
        self._topic = topic
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
        participants = [f"{item.name}/{item.role}" for item in self._all_participants]
        try:
            reply = await llm_client.complete_meeting_reply(
                topic=self._topic,
                speaker_name=self._participant.name,
                speaker_role=self._participant.role,
                speaker_prompt=self._participant.prompt,
                participants=participants,
                transcript=transcript,
            )
        except Exception:
            reply = ""
        if not reply:
            reply = self._fallback_reply()
        return Response(chat_message=TextMessage(source=self.name, content=reply))

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

    def _fallback_reply(self) -> str:
        topic = _topic_label(self._topic)
        role = self._participant.role
        if "产品" in role:
            return f"{topic}先按用户、场景、验收三块拆。"
        if "项目经理" in role:
            return f"这次先定{topic}范围、负责人和截止时间。"
        if "后端" in role or "架构" in role:
            return f"我负责把{topic}的接口边界和数据风险列清楚。"
        if "前端" in role or "UI" in role:
            return f"我补{topic}的页面状态和异常交互。"
        if "测试" in role:
            return f"我整理{topic}的验收用例和回归范围。"
        if "数据" in role:
            return f"我定义{topic}的核心指标和埋点口径。"
        return f"我补充{topic}相关依赖和执行风险。"


async def run_round_robin_meeting(
    *,
    topic: str,
    participants: list[MeetingParticipant],
    max_turns: int,
) -> list[dict[str, str]]:
    """用 AutoGen RoundRobinGroupChat 生成会议记录，再交给 Godot 逐句播放。"""
    if not participants:
        return []

    agents = [OfficeMeetingAgent(participant, topic, participants) for participant in participants]
    team = RoundRobinGroupChat(agents, max_turns=max_turns)
    result = await team.run(task=_meeting_task(topic, participants))

    transcript: list[dict[str, str]] = []
    participant_ids = {participant.worker_id for participant in participants}
    names_by_id = {participant.worker_id: participant.name for participant in participants}
    for message in result.messages:
        if not isinstance(message, TextMessage) or message.source not in participant_ids:
            continue
        text = _clean_meeting_text(str(message.content))
        if not text:
            participant = next((item for item in participants if item.worker_id == message.source), None)
            if participant:
                text = OfficeMeetingAgent(participant, topic, participants)._fallback_reply()
        transcript.append({
            "worker_id": message.source,
            "speaker": names_by_id.get(message.source, message.source),
            "text": text,
        })
    return transcript


def _meeting_task(topic: str, participants: list[MeetingParticipant]) -> str:
    roster = "、".join(f"{item.name}/{item.role}" for item in participants)
    return render("meeting_task.md", topic=topic, roster=roster)


def _topic_label(topic: str) -> str:
    cleaned = topic
    for word in ["去会议室", "开会", "会议", "讨论一下", "讨论", "聊一下", "碰一下", "问题"]:
        cleaned = cleaned.replace(word, "")
    return cleaned.strip(" ：，。") or "这个项目"


def _clean_meeting_text(text: str) -> str:
    cleaned = text.strip()
    for marker in ["<tool_call", "</tool_call", "<parameter=", "</parameter", "```"]:
        index = cleaned.find(marker)
        if index >= 0:
            cleaned = cleaned[:index].strip()
    for prefix in ["会议发言：", "发言：", "回复："]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    if any(item in cleaned for item in load_lines("meeting_banned_phrases.txt")):
        return ""
    if len(cleaned) < 6:
        return ""
    return cleaned[:64]
