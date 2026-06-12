"""OpenAI 兼容 LLM 客户端。

只保留一条 function-calling 传输通道 `_call_function_tool`，
五种业务调用复用同一传输层；提示词全部来自 app.prompt_library。
"""
import json
from typing import Any

import httpx

from app.config import settings
from app.prompt_library import load_lines, render


class MimoClient:
    """OpenAI-compatible LLM 客户端，function calling 统一走 _call_function_tool。"""

    def __init__(self) -> None:
        self._url = settings.mimo_base_url.rstrip("/") + "/chat/completions"
        self.usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    def usage_snapshot(self) -> dict[str, int]:
        return dict(self.usage_totals)

    def _record_usage(self, usage: dict[str, Any]) -> None:
        """累计官方 usage 字段返回的 token 消耗，供前端消耗条展示。"""
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key, 0)
            if isinstance(value, (int, float)):
                self.usage_totals[key] += int(value)
        self.usage_totals["calls"] += 1

    async def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if not self._enabled():
            return {}
        payload = self._base_payload(system, user, temperature=0.4, max_tokens=1024)
        message = await self._safe_post(payload, timeout=60.0)
        if not message:
            return {}
        content = (message.get("content") or message.get("reasoning_content") or "").strip()
        return self._extract_json_from_text(content)

    async def complete_agent_decision(self, system: str, user: str) -> dict[str, Any]:
        """员工决策：LLM 只产出意图（movement_type），不直接选场景坐标。"""
        return await self._call_function_tool(
            system, user, self._agent_decision_tool(),
            temperature=0.35, max_tokens=1024, timeout=60.0,
        )

    async def complete_meeting_reply(
        self,
        *,
        topic: str,
        speaker_name: str,
        speaker_role: str,
        speaker_prompt: str,
        participants: list[str],
        transcript: list[dict[str, str]],
    ) -> str:
        """会议发言专用调用：每次只让当前 speaker 回应共享会议上下文。"""
        history = "\n".join(f"{item['speaker']}: {item['text']}" for item in transcript[-10:])
        system = render("meeting_reply_system.md", speech_rules=render("natural_speech_rules.md"))
        user = render(
            "meeting_reply_user.md",
            topic=topic,
            participants=participants,
            speaker_name=speaker_name,
            speaker_role=speaker_role,
            speaker_prompt=speaker_prompt,
            history=history or "会议刚开始，还没有发言。",
        )
        data = await self._call_function_tool(
            system, user, self._meeting_reply_tool(),
            temperature=0.55, max_tokens=160, timeout=45.0,
        )
        reply = str(data.get("reply", "")).strip()
        for prefix in [f"{speaker_name}：", f"{speaker_name}:", f"{speaker_role}：", f"{speaker_role}:"]:
            if reply.startswith(prefix):
                reply = reply[len(prefix) :].strip()
        if any(text in reply for text in load_lines("meeting_banned_phrases.txt")):
            return ""
        return reply[:64]

    async def complete_team_planning_reply(
        self,
        *,
        objective: str,
        speaker_name: str,
        speaker_role: str,
        speaker_prompt: str,
        transcript: list[dict[str, str]],
    ) -> dict[str, Any]:
        """团队规划专用调用：让一个角色在共享上下文里产出可执行工作项。"""
        history = "\n".join(f"{item['speaker']}: {item['text']}" for item in transcript[-8:])
        system = render("planning_reply_system.md", speech_rules=render("natural_speech_rules.md"))
        user = render(
            "planning_reply_user.md",
            objective=objective,
            speaker_name=speaker_name,
            speaker_role=speaker_role,
            speaker_prompt=speaker_prompt,
            history=history or "还没有规划记录。",
        )
        data = await self._call_function_tool(
            system, user, self._project_plan_item_tool(),
            temperature=0.45, max_tokens=320, timeout=45.0,
        )
        return {
            "contribution": str(data.get("contribution", "")).strip()[:140],
            "task_title": str(data.get("task_title", "")).strip()[:120],
            "task_type": str(data.get("task_type", "")).strip()[:30],
            "assignee_hint": str(data.get("assignee_hint", "")).strip()[:30],
            "risk_note": str(data.get("risk_note", "")).strip()[:120],
        }

    async def complete_directive_route(self, directive_text: str) -> dict[str, Any]:
        """用 function calling 判断老板指令进入会议流还是工作规划流。"""
        data = await self._call_function_tool(
            render("directive_route_system.md"),
            f"老板指令：{directive_text}",
            self._directive_route_tool(),
            temperature=0.1, max_tokens=160, timeout=30.0,
        )
        route = str(data.get("route", "")).strip()
        if route not in ["meeting", "work"]:
            route = ""
        return {
            "route": route,
            "confidence": self._safe_float(data.get("confidence", 0.0)),
            "reason": str(data.get("reason", "")).strip()[:120],
        }

    async def complete_colleague_reply(
        self,
        *,
        requester_name: str,
        requester_role: str,
        helper_name: str,
        helper_role: str,
        helper_prompt: str,
        question: str,
        task_title: str,
        risk_note: str,
    ) -> dict[str, Any]:
        """一对一协作 handoff：目标同事按岗位给出可执行回应。"""
        system = render("colleague_reply_system.md", speech_rules=render("natural_speech_rules.md"))
        user = render(
            "colleague_reply_user.md",
            requester_name=requester_name,
            requester_role=requester_role,
            helper_name=helper_name,
            helper_role=helper_role,
            helper_prompt=helper_prompt,
            task_title=task_title,
            risk_note=risk_note or "暂无",
            question=question,
        )
        data = await self._call_function_tool(
            system, user, self._colleague_reply_tool(),
            temperature=0.45, max_tokens=260, timeout=35.0,
        )
        return {
            "reply": str(data.get("reply", "")).strip()[:90],
            "work_update": str(data.get("work_update", "")).strip()[:120],
            "risk_note": str(data.get("risk_note", "")).strip()[:100],
            "next_step": str(data.get("next_step", "")).strip()[:100],
            "confidence": self._safe_float(data.get("confidence", 0.0)),
        }

    async def _call_function_tool(
        self,
        system: str,
        user: str,
        tool: dict[str, Any],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        """统一的 function calling 传输层：构造请求、强制工具、解析参数。"""
        if not self._enabled():
            return {}
        tool_name = tool["function"]["name"]
        payload = self._base_payload(system, user, temperature=temperature, max_tokens=max_tokens)
        payload["tools"] = [tool]
        payload["tool_choice"] = {"type": "function", "function": {"name": tool_name}}
        message = await self._safe_post(payload, timeout=timeout)
        if not message:
            return {}
        data = self._extract_tool_arguments(message, tool_name)
        if data:
            return data
        content = (message.get("content") or message.get("reasoning_content") or "").strip()
        return self._extract_json_from_text(content)

    def _enabled(self) -> bool:
        return settings.llm_enabled and bool(settings.mimo_api_key)

    def _base_payload(self, system: str, user: str, *, temperature: float, max_tokens: int) -> dict[str, Any]:
        return {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }

    async def _safe_post(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """LLM 不确定性兜底：网络/限流/响应结构异常统一返回空，调用方降级规则。"""
        try:
            return await self._post(payload, timeout=timeout)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return {}

    async def _post(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()
        data = response.json()
        usage = data.get("usage")
        if isinstance(usage, dict):
            self._record_usage(usage)
        return data["choices"][0]["message"]

    def _agent_decision_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "office_agent_decision",
                "description": "给办公室模拟器提交一个员工下一步行动意图和可见思考流，具体地点由后端解析。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "movement_type": {
                            "type": "string",
                            "enum": ["own_desk", "visit_colleague", "meeting", "break", "stay"],
                            "description": "行动意图：own_desk回本人固定工位；visit_colleague找同事；meeting去会议室；break休息摸鱼；stay原地整理。",
                        },
                        "colleague_id": {
                            "type": "string",
                            "description": "movement_type=visit_colleague 时填写要找的员工ID，例如 worker1；否则空字符串",
                        },
                        "say": {"type": "string", "description": "角色自然说出的一句话，必须和 movement_type 描述同一件事"},
                        "status": {"type": "string", "description": "角色当前工作状态"},
                        "mood": {"type": "string", "description": "角色情绪"},
                        "focus_task": {"type": "string", "description": "角色当前关注任务"},
                        "intent": {"type": "string", "description": "角色为什么这么行动，作为游戏内可见思考"},
                        "work_update": {"type": "string", "description": "角色对任务的实际推进"},
                        "risk_note": {"type": "string", "description": "发现的风险，没有则空字符串"},
                        "needs_help_from": {"type": "string", "description": "需要协作的员工ID，没有则空字符串"},
                        "confirmation_question": {"type": "string", "description": "需要游戏内负责人或同事确认的问题，没有则空字符串"},
                        "memory_note": {"type": "string", "description": "写入角色短期记忆的自然语言记录"},
                        "stream_lines": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "2到5条游戏内可见思考流，不要写模型推理过程",
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
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
                        "stream_lines",
                        "confidence",
                    ],
                },
            },
        }

    def _meeting_reply_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "meeting_reply",
                "description": "提交当前会议发言人的一句真实办公室会议发言。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reply": {
                            "type": "string",
                            "description": "36字以内的一句会议发言，必须回应议题或上一位同事。",
                        },
                    },
                    "required": ["reply"],
                },
            },
        }

    def _project_plan_item_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "project_plan_item",
                "description": "提交当前岗位在项目规划中的一个结构化工作项。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contribution": {"type": "string", "description": "当前岗位补充的关键信息。"},
                        "task_title": {"type": "string", "description": "可执行任务标题。"},
                        "task_type": {
                            "type": "string",
                            "enum": ["product", "backend", "frontend", "design", "qa", "data", "ops", "general"],
                        },
                        "assignee_hint": {"type": "string", "description": "建议负责人或岗位。"},
                        "risk_note": {"type": "string", "description": "风险，没有则空字符串。"},
                    },
                    "required": ["contribution", "task_title", "task_type", "assignee_hint", "risk_note"],
                },
            },
        }

    def _directive_route_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "office_directive_route",
                "description": "把老板自然语言指令路由到会议流或工作规划流。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "route": {
                            "type": "string",
                            "enum": ["meeting", "work"],
                            "description": "meeting=正式进会议室；work=拆任务并回工位推进",
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string", "description": "简短说明路由依据。"},
                    },
                    "required": ["route", "confidence", "reason"],
                },
            },
        }

    def _colleague_reply_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "colleague_reply",
                "description": "提交一对一同事协作中的被请教者回应和结构化结果。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reply": {
                            "type": "string",
                            "description": "被请教同事说的一句话，50字以内，具体回应问题。",
                        },
                        "work_update": {"type": "string", "description": "这次沟通推动了什么工作。"},
                        "risk_note": {"type": "string", "description": "新发现或确认的风险，没有则空字符串。"},
                        "next_step": {"type": "string", "description": "下一步动作或负责人。"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["reply", "work_update", "risk_note", "next_step", "confidence"],
                },
            },
        }

    def _safe_float(self, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _extract_json_from_text(self, content: str) -> dict[str, Any]:
        if not content:
            return {}
        content = self._extract_json_object(content)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    def _extract_tool_arguments(self, message: dict[str, Any], tool_name: str) -> dict[str, Any]:
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            if function.get("name") != tool_name:
                continue
            try:
                return json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                return self._extract_json_from_text(function.get("arguments") or "")
        return {}

    def _extract_json_object(self, content: str) -> str:
        """从模型文本里提取第一段 JSON 对象，兼容模型偶尔带说明文字。"""
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return content
        return content[start : end + 1]


llm_client = MimoClient()
