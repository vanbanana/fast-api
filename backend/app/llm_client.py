"""OpenAI 兼容 LLM 客户端。

只保留一条 function-calling 传输通道 `_call_function_tool`，
五种业务调用复用同一传输层；提示词全部来自 app.prompt_library。
"""
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("llm")

from app.config import settings
from app.prompt_library import load_lines, render


class MimoClient:
    """OpenAI-compatible LLM 客户端，function calling 统一走 _call_function_tool。"""

    def __init__(self) -> None:
        self._url = settings.mimo_base_url.rstrip("/") + "/chat/completions"
        self.usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        # LLM 调用日志缓冲区，供 main.py 通过 WebSocket 推送到 Godot F12 面板
        self.log_buffer: list[dict[str, Any]] = []

    def drain_log(self) -> list[dict[str, Any]]:
        """取出并清空日志缓冲区。"""
        logs = self.log_buffer[:]
        self.log_buffer.clear()
        return logs

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

    async def generate_a2a_line(
        self,
        *,
        speaker_name: str,
        speaker_role: str,
        speaker_personality: str,
        listener_name: str,
        listener_role: str,
        directive_text: str,
        transcript_summary: str,
        turn_number: int,
    ) -> tuple[str, bool, str]:
        """A2A 同事闲聊：用 function calling 生成一句对话 + 是否结束标志 + 分配任务。

        Returns (reply_text, done, assign_task):
            reply_text — 这句台词
            done — True 表示 LLM 认为对话应该结束了
            assign_task — 如果给对方安排了任务，返回任务标题，否则空字符串
        """
        try:
            data = await self._call_function_tool(
                render("a2a_chat_system.md", speech_rules=render("natural_speech_rules.md")),
                self._build_a2a_user_prompt(
                    speaker_name, speaker_role, speaker_personality,
                    listener_name, listener_role,
                    directive_text, transcript_summary, turn_number,
                ),
                self._a2a_line_tool(),
                temperature=0.6, max_tokens=120, timeout=15.0,
            )
            reply = str(data.get("reply", "")).strip()
            done = bool(data.get("done", False))
            assign_task = str(data.get("assign_task", "")).strip()
            if not reply or len(reply) < 2:
                return "", False, ""
            return reply[:80], done, assign_task
        except Exception:
            logger.exception("[LLM] generate_a2a_line failed")
            return "", False, ""

    def _build_a2a_user_prompt(
        self,
        speaker_name: str, speaker_role: str, speaker_personality: str,
        listener_name: str, listener_role: str,
        directive_text: str, transcript_summary: str, turn_number: int,
    ) -> str:
        """构造 A2A 对话 user prompt。"""
        directive_section = ""
        if turn_number == 0 and directive_text:
            directive_section = "你的任务: %s\n如果需要给对方安排工作，请填写 assign_task 字段。" % directive_text
        transcript_section = ""
        if transcript_summary:
            transcript_section = "之前的对话:\n%s" % transcript_summary
        if turn_number == 0:
            instruction = "请说一句开场白(10-25字)，自然地打招呼并切入正题。"
        else:
            instruction = (
                "请根据对话内容给一句自然回应(10-25字)。\n"
                "如果对方已经表示要离开/结束话题，你只回一句极短告别(2-8字)，"
                "并将 done 设为 true。\n"
                "如果你是来给对方派活的，且还没填过 assign_task，现在可以补填。"
            )
        return render(
            "a2a_chat_user.md",
            speaker_name=speaker_name,
            speaker_role=speaker_role,
            personality=speaker_personality,
            listener_name=listener_name,
            listener_role=listener_role,
            directive_section=directive_section,
            transcript_section=transcript_section,
            instruction=instruction,
        )

    def _a2a_line_tool(self) -> dict[str, Any]:
        """A2A 对话 function calling tool schema — 返回 reply + done + assign_task。"""
        return {
            "type": "function",
            "function": {
                "name": "say_a2a_line",
                "description": (
                    "在面对面对话中说一句话。如果话题已结束/对方要离开，设 done=true。"
                    "如果你是主管/项目负责人，需要给对方安排具体工作任务时，填写 assign_task。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reply": {"type": "string", "description": "你要说的台词（10-25字）。"},
                        "done": {
                            "type": "boolean",
                            "description": (
                                "这句话说完后对话是否结束。"
                                "当对方表示要离开、话题已经聊完、或者你说了告别语时设为 true。"
                                "正常继续聊天时设为 false。"
                            ),
                        },
                        "assign_task": {
                            "type": "string",
                            "description": (
                                "如果你要给对方安排一个具体工作任务，填写任务标题（10字以内）。"
                                "只在你是主管/需要分配工作时填写，否则留空。"
                                "填写后对方会真实执行这个任务。"
                            ),
                        },
                    },
                    "required": ["reply", "done"],
                },
            },
        }

    def _meeting_speak_tool(self) -> dict[str, Any]:
        """会议发言 function calling tool schema — 返回 reply + done + prd_point。"""
        return {
            "type": "function",
            "function": {
                "name": "speak_in_meeting",
                "description": "在会议中发言。如果你认为讨论已经充分，可以结束会议并给出 PRD 总结。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reply": {
                            "type": "string",
                            "description": "你的发言内容（15-40字），简短有力的专业意见。",
                        },
                        "done": {
                            "type": "boolean",
                            "description": (
                                "是否提议结束本次会议。"
                                "只有当你是主管/项目经理，且讨论已充分覆盖所有关键问题时才设为 true。"
                                "普通参会者始终设为 false。"
                            ),
                        },
                        "prd_point": {
                            "type": "string",
                            "description": (
                                "如果 done=true，填写本次会议的 PRD 要点总结（50-150字）。"
                                "包含：目标、关键决策、分工、风险、下一步。"
                                "如果 done=false，填空字符串。"
                            ),
                        },
                    },
                    "required": ["reply", "done", "prd_point"],
                },
            },
        }

    async def generate_meeting_turn(
        self,
        *,
        speaker_name: str,
        speaker_role: str,
        topic: str,
        roster: str,
        transcript_summary: str,
        turn_number: int,
        is_lead: bool = False,
    ) -> tuple[str, bool, str]:
        """会议逐轮发言：用 function calling 生成一句 + 是否结束 + PRD 要点。

        Returns (reply_text, done, prd_point):
            reply_text  — 发言内容
            done        — True 表示提议结束会议
            prd_point   — PRD 要点（仅 done=True 时有值）
        """
        try:
            session_state = "第 %d 轮发言" % (turn_number + 1)
            data = await self._call_function_tool(
                render("meeting_discuss_system.md",
                       speaker_name=speaker_name, speaker_role=speaker_role,
                       topic=topic, roster=roster,
                       speech_rules=render("natural_speech_rules.md")),
                render("meeting_discuss_user.md",
                       speaker_name=speaker_name, speaker_role=speaker_role,
                       topic=topic, session_state=session_state,
                       transcript_section=self._meeting_transcript_section(transcript_summary)),
                self._meeting_speak_tool(),
                temperature=0.6, max_tokens=200, timeout=20.0,
            )
            reply = str(data.get("reply", "")).strip()
            done = bool(data.get("done", False)) if is_lead else False
            prd = str(data.get("prd_point", "")).strip()
            if not reply or len(reply) < 4:
                return "", False, ""
            return reply[:80], done, prd[:300]
        except Exception:
            logger.exception("[LLM] generate_meeting_turn failed")
            return "", False, ""

    @staticmethod
    def _meeting_transcript_section(transcript: str) -> str:
        if not transcript:
            return ""
        return "\n【之前的发言记录】\n%s" % transcript

    async def generate_boss_reply(
        self,
        *,
        worker_name: str,
        worker_role: str,
        worker_personality: str,
        boss_message: str,
        memory_context: str = "",
    ) -> str:
        """老板直接 @ 员工聊天时，员工回复。和 A2A 同事对话完全不同的 prompt。"""
        try:
            context_section = ""
            if memory_context:
                context_section = f"\n【你的记忆上下文】\n{memory_context}\n"
            system = render(
                "boss_reply_system.md",
                worker_name=worker_name,
                role=worker_role,
                personality=worker_personality,
                speech_rules=render("natural_speech_rules.md"),
                context_section=context_section,
            )
            user = render(
                "boss_reply_user.md",
                worker_name=worker_name,
                role=worker_role,
                personality=worker_personality,
                boss_message=boss_message,
                context_section=context_section,
            )
            text = await self._call_chat(system, user, temperature=0.5, max_tokens=100, timeout=20.0)
            return text.strip()[:100] if text else ""
        except Exception:
            logger.exception("[LLM] generate_boss_reply failed")
            return ""

    async def _call_chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> str:
        """简单聊天调用：返回模型文本内容，不走 function calling。"""
        if not self._enabled():
            return ""
        payload = self._base_payload(system, user, temperature=temperature, max_tokens=max_tokens)
        message = await self._safe_post(payload, timeout=timeout)
        if not message:
            return ""
        return (message.get("content") or message.get("reasoning_content") or "").strip()

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
            logger.warning("[TOOL] LLM 未启用或无 API Key")
            return {}
        tool_name = tool["function"]["name"]
        payload = self._base_payload(system, user, temperature=temperature, max_tokens=max_tokens)
        payload["tools"] = [tool]
        payload["tool_choice"] = {"type": "function", "function": {"name": tool_name}}
        message = await self._safe_post(payload, timeout=timeout)
        if not message:
            logger.warning("[TOOL] %s _safe_post 返回空", tool_name)
            return {}
        # 打印原始 message 结构（只打关键字段，不打完整内容）
        msg_keys = list(message.keys())
        has_tool_calls = bool(message.get("tool_calls"))
        content_preview = (message.get("content") or "")[:80]
        logger.info(
            "[TOOL] %s | message_keys=%s | tool_calls=%s | content=%s",
            tool_name, msg_keys, has_tool_calls, repr(content_preview),
        )
        data = self._extract_tool_arguments(message, tool_name)
        if data:
            logger.info("[TOOL] %s extract_tool_arguments 成功: %s", tool_name, data)
            return data
        content = (message.get("content") or message.get("reasoning_content") or "").strip()
        fallback = self._extract_json_from_text(content)
        if fallback:
            logger.info("[TOOL] %s 从 content 提取 JSON 成功: %s", tool_name, fallback)
        else:
            logger.warning("[TOOL] %s 所有提取方式都失败，返回空", tool_name)
        return fallback

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
        # 从 user message 提取调用类型标签（取前 30 字符）
        call_tag = "?"
        msgs = payload.get("messages", [])
        if msgs:
            last = msgs[-1]
            content = last.get("content", "")
            if isinstance(content, str) and content:
                call_tag = content[:30].replace("\n", " ")
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()
        data = response.json()
        usage = data.get("usage")
        entry: dict[str, Any] = {"tag": call_tag, "tokens_in": 0, "tokens_out": 0, "result": ""}
        if isinstance(usage, dict):
            self._record_usage(usage)
            entry["tokens_in"] = usage.get("prompt_tokens", 0)
            entry["tokens_out"] = usage.get("completion_tokens", 0)
            logger.info("[LLM] %s | tokens: in=%d out=%d total=%d",
                        call_tag,
                        entry["tokens_in"], entry["tokens_out"],
                        usage.get("total_tokens", 0))
        msg = data["choices"][0]["message"]
        # 记录返回的关键字段
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name", "?")
                args_str = json.dumps((tc.get("function") or {}).get("args", {}), ensure_ascii=False)[:80]
                entry["result"] = "%s(%s)" % (fn, args_str)
                logger.info("[LLM→] %s(%s)", fn, args_str)
        else:
            say = (msg.get("content") or "").strip()[:60]
            if say:
                entry["result"] = say
                logger.info("[LLM→] say=%s", say)
        # 写入缓冲区，供 main.py 通过 WebSocket 推送到 Godot F12 面板
        self.log_buffer.append(entry)
        return msg

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

    def _boss_intent_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "classify_boss_intent",
                "description": "解析老板指令的意图类型：找人、开会、还是纯聊天。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["seek_worker", "start_meeting", "reply_chat"],
                            "description": "seek_worker=找人办事；start_meeting=开会；reply_chat=直接聊天/提问",
                        },
                        "actor_name": {"type": "string", "description": "被指派执行动作的员工名字（seek_worker 时是去找人的那个）"},
                        "target_name": {"type": "string", "description": "动作目标（被找的人 / 被聊天的对象）"},
                        "meeting_topic": {"type": "string", "description": "会议主题，仅 start_meeting 时填写"},
                        "reason": {"type": "string", "description": "简短说明判断依据。"},
                    },
                    "required": ["intent", "reason"],
                },
            },
        }

    async def complete_boss_intent(self, directive_text: str) -> dict[str, Any]:
        """用 function calling 解析老板指令意图（找人/开会/聊天）。"""
        # 在 user prompt 中附带员工名单，让 LLM 返回精确的名字
        from app.runtime import office_runtime
        agent_list = ", ".join(f"{a.name}({a.role})" for a in office_runtime.agents.values())
        user_prompt = f"公司员工: {agent_list}\n老板指令：{directive_text}"
        data = await self._call_function_tool(
            render("boss_intent_system.md"),
            user_prompt,
            self._boss_intent_tool(),
            temperature=0.1, max_tokens=200, timeout=30.0,
        )
        print("[BOSS INTENT RAW] _call_function_tool 原始返回: %s" % repr(data))
        intent = str(data.get("intent", "")).strip()
        if intent not in ["seek_worker", "start_meeting", "reply_chat"]:
            intent = ""
        return {
            "intent": intent,
            "actor_name": str(data.get("actor_name", "")).strip(),
            "target_name": str(data.get("target_name", "")).strip(),
            "meeting_topic": str(data.get("meeting_topic", "")).strip(),
            "reason": str(data.get("reason", "")).strip()[:120],
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
    def _task_assign_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "assign_tasks",
                "description": "为所有参会员工一次性分配具体可执行的任务项。每个人一个任务。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "assignments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "worker_id": {"type": "string", "description": "员工ID，如 worker1"},
                                    "task_title": {"type": "string", "description": "具体可执行的任务标题"},
                                    "task_type": {
                                        "type": "string",
                                        "enum": ["product", "backend", "frontend", "design", "qa", "data", "ops", "general"],
                                    },
                                    "contribution": {"type": "string", "description": "这个人的具体贡献说明"},
                                    "risk_note": {"type": "string", "description": "风险，没有则空字符串"},
                                },
                                "required": ["worker_id", "task_title", "task_type", "contribution", "risk_note"],
                            },
                        },
                    },
                    "required": ["assignments"],
                },
            },
        }

    async def generate_task_planning(self, objective: str, participants: list[dict]) -> list[dict[str, str]]:
        """用 function calling 为所有参与者生成任务项（替代 AutoGen RoundRobinGroupChat）。"""
        participant_lines = []
        for i, p in enumerate(participants, 1):
            participant_lines.append(
                f"{i}. {p.get('name', '?')} ({p.get('role', '?')}) - ID: {p.get('worker_id', '?')}"
            )

        user_msg = (
            f"项目目标：{objective}\n\n"
            f"参会人员：\n" + "\n".join(participant_lines) + "\n\n"
            f"请为每个人分配一个具体的可执行任务。必须调用 assign_tasks 工具。"
        )

        data = await self._call_function_tool(
            render("task_planning_system.md"),
            user_msg,
            self._task_assign_tool(),
            temperature=0.3, max_tokens=1200, timeout=60.0,
        )

        assignments = data.get("assignments", [])
        if not assignments:
            # LLM 可能返回单个 assignment 而不是列表
            if data.get("task_title"):
                return [{
                    "worker_id": "",
                    "task_title": str(data.get("task_title", "")).strip(),
                    "task_type": str(data.get("task_type", "general")).strip() or "general",
                    "contribution": str(data.get("contribution", "")).strip(),
                    "risk_note": str(data.get("risk_note", "")).strip(),
                }]
            return []

        results = []
        for item in assignments:
            if isinstance(item, dict):
                results.append({
                    "worker_id": str(item.get("worker_id", "")).strip(),
                    "task_title": str(item.get("task_title", "")).strip(),
                    "task_type": str(item.get("task_type", "general")).strip() or "general",
                    "contribution": str(item.get("contribution", "")).strip(),
                    "risk_note": str(item.get("risk_note", "")).strip(),
                })
        logger.info("[TASK_PLAN] 拆解完成: %d 个任务", len(results))
        return results

    async def generate_meeting_prd_summary(self, lead_name: str, topic: str,
                                            full_transcript: str) -> str:
        """用 LLM 根据会议完整记录生成详细 PRD（会议关闭阶段主管发言用）。"""
        system_prompt = (
            "你是项目主持人%s。现在团队讨论已经充分，你需要输出一份完整的、可直接交付的 PRD 文档。\n\n"
            "PRD 必须包含以下所有部分，每部分都要详尽具体：\n"
            "## 1. 项目背景与目标（3-5句话）\n"
            "## 2. 核心功能需求（分点列出每个功能点，含优先级 P0/P1/P2）\n"
            "## 3. 技术方案概述（架构、技术选型、关键决策）\n"
            "## 4. 任务拆解与分工（明确到人：谁做什么、交付物是什么、依赖关系）\n"
            "## 5. 风险与注意事项（技术风险、时间风险、缓解方案）\n"
            "## 6. 验收标准（什么算完成、如何验收）\n\n"
            "格式要求：使用 Markdown 格式，内容要非常详细、专业、可执行。\n"
            "总字数要求：300-600字以上。直接输出 PRD 内容，不要加前缀说明。" % lead_name
        )
        user_msg = (
            "会议主题：%s\n\n"
            "会议完整记录：\n%s\n\n"
            "请根据以上讨论内容，输出完整的 PRD 文档：" % (topic, full_transcript[-3000:])
        )

        try:
            result = await self._call_function_tool(
                system_prompt,
                user_msg,
                self._prd_summary_tool(),
                temperature=0.5, max_tokens=2000, timeout=60.0,
            )
            summary = str(result.get("summary", "")).strip()
            if summary:
                logger.info("[MEETING_PRD] PRD生成成功(%d字): %s", len(summary), summary[:80])
                return summary
        except Exception as e:
            logger.warning("[MEETING_PRD] PRD生成失败: %s", e)
        return ""

    def _prd_summary_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "output_prd_summary",
                "description": "输出会议的PRD总结内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "PRD总结文本",
                        },
                    },
                    "required": ["summary"],
                },
            },
        }


    async def generate_work_update(
        self,
        *,
        name: str,
        role: str,
        task_title: str,
        progress_pct: float,
        last_status: str = "",
    ) -> str:
        """生成员工实时工作状态描述（function calling，LLM 自主决定内容）。"""
        try:
            data = await self._call_function_tool(
                render("work_update_system.md"),
                render("work_update_user.md",
                       name=name, role=role, task_title=task_title,
                       progress_pct=int(progress_pct * 100),
                       last_status=last_status or "（刚开始）"),
                self._work_update_tool(),
                temperature=0.6, max_tokens=120, timeout=6.0,
            )
            status = str(data.get("status_text", "")).strip()
            return status[:30] if status else ""
        except Exception:
            logger.exception("[LLM] generate_work_update failed")
            return ""

    def _work_update_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "report_work_update",
                "description": "汇报当前工作状态",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status_text": {
                            "type": "string",
                            "description": "此刻的工作状态描述（15字以内），如：'正在设计数据库Schema'、'刚跑完单元测试'",
                        },
                        "blocker": {
                            "type": "string",
                            "description": "是否有卡点（无则留空）",
                        },
                    },
                    "required": ["status_text", "blocker"],
                },
            },
        }


llm_client = MimoClient()
