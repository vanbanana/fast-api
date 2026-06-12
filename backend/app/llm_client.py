import json
from typing import Any

import httpx

from app.config import settings


class MimoClient:
    """OpenAI-compatible LLM 客户端，用于角色决策测试。"""

    def __init__(self) -> None:
        self._url = settings.mimo_base_url.rstrip("/") + "/chat/completions"

    async def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if not settings.llm_enabled or not settings.mimo_api_key:
            return {}

        payload = {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            "max_completion_tokens": 1024,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        message = response.json()["choices"][0]["message"]
        content = (message.get("content") or message.get("reasoning_content") or "").strip()
        if not content:
            return {}

        content = self._extract_json_object(content)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    async def complete_agent_decision(self, system: str, user: str, target_ids: list[str]) -> dict[str, Any]:
        """使用 MiMo/OpenAI 兼容 function calling，稳定拿到员工动作结构。"""
        if not settings.llm_enabled or not settings.mimo_api_key:
            return {}

        payload = {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [self._agent_decision_tool(target_ids)],
            "tool_choice": {"type": "function", "function": {"name": "office_agent_decision"}},
            "temperature": 0.35,
            "max_completion_tokens": 1024,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        message = response.json()["choices"][0]["message"]
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            if function.get("name") != "office_agent_decision":
                continue
            try:
                return json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                return self._extract_json_from_text(function.get("arguments") or "")

        content = (message.get("content") or message.get("reasoning_content") or "").strip()
        return self._extract_json_from_text(content)

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
        if not settings.llm_enabled or not settings.mimo_api_key:
            return ""

        history = "\n".join(f"{item['speaker']}: {item['text']}" for item in transcript[-10:])
        system = (
            "你在模拟真实软件公司会议，不是聊天机器人。"
            "你只能作为当前发言人说一句话，必须回应会议议题或上一位同事，不能自说自话。"
            "必须调用 meeting_reply 工具提交发言。"
            "reply 要像真实同事短句，32字以内，不要官腔，不要重复别人原话。"
            "禁止使用“最好同步一下”“不然容易各做各的”“我先确认一下状态”“大家先对齐一下”这类空话。"
            "必须带出本岗位的具体工作：目标、范围、接口、页面、测试、数据或排期之一。"
            "句子里必须有可执行对象，例如字段、验收口径、页面状态、接口边界、回归范围、指标口径、截止时间。"
        )
        user = (
            f"会议议题：{topic}\n"
            f"参会人：{participants}\n"
            f"当前发言人：{speaker_name} / {speaker_role}\n"
            f"发言人人设：{speaker_prompt}\n"
            f"已有会议记录：\n{history or '会议刚开始，还没有发言。'}\n"
            "请给当前发言人生成下一句会议发言。"
        )
        payload = {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [self._meeting_reply_tool()],
            "tool_choice": {"type": "function", "function": {"name": "meeting_reply"}},
            "temperature": 0.55,
            "max_completion_tokens": 160,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=45.0, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        message = response.json()["choices"][0]["message"]
        data = self._extract_tool_arguments(message, "meeting_reply")
        if not data:
            content = (message.get("content") or message.get("reasoning_content") or "").strip()
            data = self._extract_json_from_text(content)
        reply = str(data.get("reply", "")).strip()
        for prefix in [f"{speaker_name}：", f"{speaker_name}:", f"{speaker_role}：", f"{speaker_role}:"]:
            if reply.startswith(prefix):
                reply = reply[len(prefix) :].strip()
        banned = ["最好同步一下", "不然容易各做各的", "我先确认一下状态", "同步一下", "各做各的", "大家先对齐", "我先了解"]
        if any(text in reply for text in banned):
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
        if not settings.llm_enabled or not settings.mimo_api_key:
            return {}

        history = "\n".join(f"{item['speaker']}: {item['text']}" for item in transcript[-8:])
        system = (
            "你在模拟真实软件公司的项目规划会。"
            "你只能作为当前岗位补充自己的工作项、风险或依赖。"
            "必须调用 project_plan_item 工具提交结构化工作项。"
            "task_type 只能是 product/backend/frontend/design/qa/data/ops/general。"
            "task_title 要是可执行任务，不要空泛。"
        )
        user = (
            f"老板目标：{objective}\n"
            f"当前发言人：{speaker_name} / {speaker_role}\n"
            f"发言人人设：{speaker_prompt}\n"
            f"已有规划记录：\n{history or '还没有规划记录。'}\n"
            "请输出当前岗位应该补充的一个关键任务。"
        )
        payload = {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [self._project_plan_item_tool()],
            "tool_choice": {"type": "function", "function": {"name": "project_plan_item"}},
            "temperature": 0.45,
            "max_completion_tokens": 320,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=45.0, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        message = response.json()["choices"][0]["message"]
        data = self._extract_tool_arguments(message, "project_plan_item")
        if not data:
            content = (message.get("content") or message.get("reasoning_content") or "").strip()
            data = self._extract_json_from_text(content)
        return {
            "contribution": str(data.get("contribution", "")).strip()[:140],
            "task_title": str(data.get("task_title", "")).strip()[:120],
            "task_type": str(data.get("task_type", "")).strip()[:30],
            "assignee_hint": str(data.get("assignee_hint", "")).strip()[:30],
            "risk_note": str(data.get("risk_note", "")).strip()[:120],
        }

    async def complete_directive_route(self, directive_text: str) -> dict[str, Any]:
        """用 function calling 判断老板指令进入会议流还是工作规划流。"""
        if not settings.llm_enabled or not settings.mimo_api_key:
            return {}

        system = (
            "你是办公室模拟器的指令路由器，只负责分类，不参与聊天。"
            "必须调用 office_directive_route 工具。"
            "meeting 表示玩家明确要求员工去会议室/开会/当面集体讨论。"
            "work 表示玩家给业务目标、开发任务、修 bug、上线目标或让团队自行推进。"
            "如果只是普通业务目标里有“讨论/同步/对齐”等词，但没有明确要求进入会议室，也优先 work。"
        )
        user = f"老板指令：{directive_text}"
        payload = {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [self._directive_route_tool()],
            "tool_choice": {"type": "function", "function": {"name": "office_directive_route"}},
            "temperature": 0.1,
            "max_completion_tokens": 160,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        message = response.json()["choices"][0]["message"]
        data = self._extract_tool_arguments(message, "office_directive_route")
        if not data:
            content = (message.get("content") or message.get("reasoning_content") or "").strip()
            data = self._extract_json_from_text(content)
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
        if not settings.llm_enabled or not settings.mimo_api_key:
            return {}

        system = (
            "你在模拟真实软件公司里的一对一当面协作，不是聊天机器人。"
            "当前只有被请教的同事发言，必须调用 colleague_reply 工具。"
            "回应要具体、短句，能推动任务，不要寒暄，不要空泛同步。"
            "如果信息不足，给出下一步负责人或需要补齐的字段。"
        )
        user = (
            f"发起人：{requester_name} / {requester_role}\n"
            f"被请教同事：{helper_name} / {helper_role}\n"
            f"被请教同事人设：{helper_prompt}\n"
            f"当前任务：{task_title}\n"
            f"当前风险：{risk_note or '暂无'}\n"
            f"发起人的问题：{question}\n"
            "请让被请教同事给一句回应，并补充结构化协作结果。"
        )
        payload = {
            "model": settings.mimo_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [self._colleague_reply_tool()],
            "tool_choice": {"type": "function", "function": {"name": "colleague_reply"}},
            "temperature": 0.45,
            "max_completion_tokens": 260,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {settings.mimo_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=35.0, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()

        message = response.json()["choices"][0]["message"]
        data = self._extract_tool_arguments(message, "colleague_reply")
        if not data:
            content = (message.get("content") or message.get("reasoning_content") or "").strip()
            data = self._extract_json_from_text(content)
        return {
            "reply": str(data.get("reply", "")).strip()[:90],
            "work_update": str(data.get("work_update", "")).strip()[:120],
            "risk_note": str(data.get("risk_note", "")).strip()[:100],
            "next_step": str(data.get("next_step", "")).strip()[:100],
            "confidence": self._safe_float(data.get("confidence", 0.0)),
        }

    def _agent_decision_tool(self, target_ids: list[str]) -> dict[str, Any]:
        target_schema: dict[str, Any] = {"type": "string"}
        if target_ids:
            target_schema["enum"] = target_ids
        return {
            "type": "function",
            "function": {
                "name": "office_agent_decision",
                "description": "给办公室模拟器提交一个员工下一步行动和可见思考流。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_id": target_schema,
                        "movement_type": {
                            "type": "string",
                            "enum": ["own_desk", "visit_colleague", "meeting", "break", "stay"],
                            "description": "稳定工具动作：own_desk回本人固定工位；visit_colleague找同事；meeting去会议室；break休息摸鱼；stay原地整理。",
                        },
                        "colleague_id": {
                            "type": "string",
                            "description": "movement_type=visit_colleague 时填写要找的员工ID，例如 worker1；否则空字符串",
                        },
                        "say": {"type": "string", "description": "角色自然说出的一句话"},
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
