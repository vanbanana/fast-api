from app.domain import BossDirective, CompanyProject, OfficeTargets
from app.llm_client import llm_client
from app.memory import memory_store
from app.meeting_session import MeetingSession
from app.meeting_runtime import MeetingRuntime
from app.office_clock import office_clock
from app.planning_service import ProjectPlanningService
from app.schemas import AgentCommand, AgentSnapshot, BossCommand, CompanySnapshot, ProjectTaskSnapshot, WorkerEvent
from app.worker_agent import OfficeAgent
from app.worker_profile_loader import load_agent_profiles, make_agent
from app.a2a_session import A2ASession, A2ASessionManager, SessionState
from app.schemas import A2AEvent


class OfficeRuntime:
    """轻量多 agent runtime，先服务当前 Godot demo，后续可平滑升级为任务图框架。"""

    def __init__(self) -> None:
        self.targets = OfficeTargets()
        self.company = CompanyProject()
        self.directives: list[BossDirective] = []
        self.agents: dict[str, OfficeAgent] = load_agent_profiles()
        self.planning = ProjectPlanningService(self.company, self.agents)
        self.meeting = MeetingRuntime(self.company, self.targets, self.agents, self.planning)
        self._a2a_manager = A2ASessionManager()
        self._work_status_cache: dict[str, dict] = {}  # LLM工作状态缓存: {worker_id: {text, threshold}}
        self._scene_initialized: bool = False  # 是否已收到过 world_snapshot

    @property
    def active_meeting(self) -> MeetingSession | None:
        return self.meeting.active_session

    async def handle_event(self, event: WorkerEvent) -> list[AgentCommand]:
        if event.type == "world_snapshot":
            self._update_targets(event)
            # 每次 world_snapshot 都重置（Godot 重启/重连时都会发）
            self._reset_runtime_for_new_scene()
            self._scene_initialized = True
            return (
                [AgentCommand(worker_id=event.worker_id, action="idle", say="后端已同步场景目标。")]
                + self.profile_commands()
            )

        if event.type == "task_progress":
            return await self._handle_task_progress(event)

        if event.type == "boss_command":
            command = BossCommand.model_validate(event.payload)
            return await self.apply_boss_command(command)

        if event.type == "a2a_event":
            return await self._handle_a2a_event(event)

        if self.meeting.is_meeting_event(event):
            meeting_commands = await self.meeting.handle_event(event)
            if meeting_commands or self.meeting.consumes_event(event):
                return meeting_commands
        if self.meeting.locks_worker(event.worker_id):
            return [self.meeting.locked_worker_command(event)]

        agent = self._get_or_create_agent(event.worker_id)
        return [AgentCommand(worker_id=agent.worker_id, action="idle", say="")]

    # ===== A2A 对话协议（AutoGen Two-Agent Chat + Google A2A Task Lifecycle）=====

    async def _handle_a2a_event(self, event: WorkerEvent) -> list[AgentCommand]:
        """处理 A2A 对话事件（参考 AutoGen Two-Agent Chat + Google A2A Task Lifecycle）。"""
        try:
            a2a = A2AEvent.model_validate(event.payload)
        except Exception:
            print("[A2A] 无效的 A2A 事件: %s" % str(event.payload)[:100])
            return []

        if a2a.event == "chat_started":
            return await self._on_a2a_chat_started(a2a)
        if a2a.event == "chat_turn":
            return await self._on_a2a_chat_turn(a2a)
        elif a2a.event == "chat_timeout":
            return self._on_a2a_chat_timeout(a2a)
        return []

    async def _on_a2a_chat_started(self, a2a: A2AEvent) -> list[AgentCommand]:
        """chat_started: 创建 session，生成发起人第一句台词。"""
        session = self._a2a_manager.create_session(
            initiator_id=a2a.speaker_id,
            responder_id=a2a.listener_id,
            directive_text=a2a.directive_text,
        )
        if session is None:
            # 并发冲突：某一方已在对话中
            print("[A2A] 并发冲突: %s/%s 已在对话中" % (a2a.speaker_id, a2a.listener_id))
            return []

        session.activate()
        print("[A2A] 对话开始: %s → %s | session=%s | 指令: %s" % (
            a2a.speaker_id, a2a.listener_id, session.session_id, a2a.directive_text[:40]))

        # Set both agents to "沟通中" and store responder's previous status
        initiator = self.agents.get(a2a.speaker_id)
        responder = self.agents.get(a2a.listener_id)
        if initiator:
            initiator.status = "沟通中"
        if responder:
            session._interrupted_status_before_chat = responder.status
            responder.status = "沟通中"
        # Generate first line command
        commands = await self._generate_a2a_line(session)
        # Add idle sync commands for both agents to push status updates
        if initiator:
            commands.append(self._idle_command_for(initiator))
        if responder:
            commands.append(self._idle_command_for(responder))
        return commands

    async def _on_a2a_chat_turn(self, a2a: A2AEvent) -> list[AgentCommand]:
        """chat_turn: 记录上一句台词，检查终止条件，生成下一句。"""
        # 通过 worker 组合查找 session
        session = self._a2a_manager.get_session_by_workers(a2a.speaker_id, a2a.listener_id)
        if session is None or not session.is_active:
            print("[A2A] chat_turn 但无活跃 session: %s ↔ %s" % (a2a.speaker_id, a2a.listener_id))
            return self._a2a_end_command(session or A2ASession(initiator_id=a2a.speaker_id, responder_id=a2a.listener_id), "failed")

        # 记录上一句到 transcript
        if a2a.last_text and a2a.last_sayer_id:
            # 避免重复记录（如果后端已经 record_line 过了）
            if not session.transcript or session.transcript[-1].content != a2a.last_text:
                session.record_line(a2a.last_sayer_id, a2a.last_text)

        # 检查终止条件（max_turns / 语义循环 — 不做关键词匹配）
        if session.should_terminate():
            session.complete()
            print("[A2A] 对话自然结束: %s ↔ %s (%d轮)" % (
                session.initiator_id, session.responder_id, session.turn_count))
            return self._a2a_end_command(session, "completed")

        # 生成下一句
        return await self._generate_a2a_line(session)

    def _on_a2a_chat_timeout(self, a2a: A2AEvent) -> list[AgentCommand]:
        """chat_timeout: Godot 端超时，清理 session。"""
        session = self._a2a_manager.get_session_by_workers(a2a.speaker_id, a2a.listener_id)
        if session is not None and session.is_active:
            session.timeout()
            self._a2a_manager.remove_session(session.session_id)
            print("[A2A] 对话超时: %s ↔ %s" % (a2a.speaker_id, a2a.listener_id))
        return self._a2a_end_command(session or A2ASession(initiator_id=a2a.speaker_id, responder_id=a2a.listener_id), "timeout")

    async def _generate_a2a_line(self, session: A2ASession) -> list[AgentCommand]:
        """为当前发言人调用 LLM 生成一句台词。"""
        speaker_id = session.current_speaker_id
        speaker = self.agents.get(speaker_id)
        listener_id = session.current_listener_id
        listener = self.agents.get(listener_id)

        if not speaker or not listener:
            session.fail()
            return [self._a2a_end_command(session, "failed")]

        line = ""
        done = False
        try:
            from app.llm_client import llm_client
            # 拼接记忆上下文 + 当前对话转录
            speaker_memory = memory_store.build_context(speaker_id)
            full_context = speaker_memory
            if session.get_transcript_summary():
                full_context += "\n\n【当前对话记录】\n" + session.get_transcript_summary()
            line, done, assign_task = await llm_client.generate_a2a_line(
                speaker_name=speaker.name,
                speaker_role=speaker.role,
                speaker_personality=speaker.personality,
                listener_name=listener.name,
                listener_role=listener.role,
                directive_text=session.directive_text,
                transcript_summary=full_context,
                turn_number=session.turn_count,
            )
        except Exception as e:
            print("[A2A] LLM 调用失败: %s" % e)
            line, done, assign_task = "", False, ""

        # 记录 LLM 分配的任务到 session
        if assign_task:
            session.assigned_task_title = assign_task
            print("[A2A] %s 给 %s 分配任务: %s" % (speaker_id, listener_id, assign_task))

        # 错误恢复：LLM 失败直接终止对话
        if not line or len(line.strip()) < 3:
            should_stop = session.record_llm_failure()
            if should_stop:
                print("[A2A] LLM 连续 %d 次失败，终止对话" % session.llm_fail_count)
                return self._a2a_end_command(session, "failed")
            # 单次失败不返回假台词，等下一轮重试
            return [self._idle_command_for(speaker)]
        else:
            session.record_llm_success()

        # 记录到 transcript
        session.record_line(speaker_id, line)

        print("[A2A] 第%d轮 %s(%s): %s [done=%s]" % (
            session.turn_count, speaker.name, speaker_id, line[:40], done))

        # 用 LLM 自己的 done 判断是否终止（不是关键词匹配）
        if session.should_terminate(llm_done=done):
            session.complete()
            commands = [self._a2a_line_command(session, speaker_id, line)]
            commands.extend(self._a2a_end_command(session, "completed"))
            return commands

        return [self._a2a_line_command(session, speaker_id, line)]

    def _a2a_line_command(self, session: A2ASession, worker_id: str, text: str) -> AgentCommand:
        """构造 chat_line 命令。"""
        return AgentCommand(
            worker_id=worker_id,
            action="chat_line",
            say=text,
            payload={
                "session_id": session.session_id,
                "turn": session.turn_count,
                "speaker_id": session.initiator_id,
                "listener_id": session.responder_id,
                "display_seconds": 4.0,
            },
        )

    def _a2a_end_command(self, session: A2ASession, reason: str = "completed") -> list[AgentCommand]:
        """构造 chat_end 命令并同步状态快照。"""
        commands: list[AgentCommand] = []
        if session.is_active:
            if reason == "failed":
                session.fail()
            elif reason == "timeout":
                session.timeout()
            else:
                session.complete()
        # Clean up session reference
        self._a2a_manager.remove_session(session.session_id)
        print("[A2A] 对话结束: %s ↔ %s (%s, %d轮)" % (
            session.initiator_id, session.responder_id, reason, session.turn_count))
        # Chat end command
        chat_end_cmd = AgentCommand(
            worker_id=session.initiator_id,
            action="chat_end",
            say="",
            payload={
                "session_id": session.session_id,
                "speaker_id": session.initiator_id,
                "listener_id": session.responder_id,
                "total_turns": session.turn_count,
                "reason": reason,
            },
        )
        commands.append(chat_end_cmd)
        # Finish errand for initiator if needed
        initiator = self.agents.get(session.initiator_id)
        responder = self.agents.get(session.responder_id)
        if initiator:
            initiator.finish_errand()
            commands.append(self._idle_command_for(initiator))
        if responder:
            # 如果对话中分配了任务，创建任务并绑定给 responder
            if session.assigned_task_title and reason == "completed":
                task = self.company.create_task(
                    title=session.assigned_task_title,
                    task_type="general",
                    priority=2,
                    created_by=f"a2a:{session.initiator_id}",
                    assignee_id=session.responder_id,
                )
                responder.active_task_id = task.task_id
                responder.status = "工作中"
                responder.focus_task = task.title
                responder.remember(f"收到{initiator.name if initiator else session.initiator_id}分配的任务:{task.title}")
                print("[A2A] 创建任务 %s → %s: %s" % (task.task_id, session.responder_id, task.title))
                # 推送 task_update
                commands.append(AgentCommand(
                    worker_id="office",
                    action="task_update",
                    say="",
                    payload={"tasks": [t.snapshot() for t in self.company.tasks.values()]},
                ))
            else:
                # Restore responder's previous status
                responder.status = session._interrupted_status_before_chat or "在办公室待命"
            commands.append(self._idle_command_for(responder))
        # 双方记录对话到记忆（方便后续上下文感知）
        transcript_brief = session.get_transcript_summary()[:120]
        initiator_name = initiator.name if initiator else session.initiator_id
        responder_name = responder.name if responder else session.responder_id
        memory_store.remember(
            session.initiator_id,
            f"和{responder_name}面对面聊了{session.turn_count}轮：{transcript_brief}",
            kind="a2a_chat",
        )
        memory_store.remember(
            session.responder_id,
            f"和{initiator_name}面对面聊了{session.turn_count}轮：{transcript_brief}",
            kind="a2a_chat",
        )
        return commands

    def _idle_command_for(self, agent: OfficeAgent) -> AgentCommand:
        """Create an idle command that carries the agent's current snapshot for UI sync."""
        payload = agent.snapshot().model_dump()
        payload["decision_source"] = "status_sync"
        return AgentCommand(
            worker_id=agent.worker_id,
            action="idle",
            say="",
            payload=payload,
        )
    # ===== 老板指令处理 =====

    async def apply_boss_command(self, command: BossCommand) -> list[AgentCommand]:
        directive = BossDirective(
            text=command.text,
            priority=command.priority,
            target_worker_ids=command.target_worker_ids,
        )
        self.directives.append(directive)
        self.directives = self.directives[-20:]

        # 统一用 LLM function calling 解析意图（找人 / 开会 / 聊天）
        intent = await self._classify_boss_intent(directive.text)

        if not intent.get("intent"):
            print("[BOSS] LLM 未返回有效意图，跳过指令: %s" % directive.text[:60])
            return []

        if intent["intent"] == "seek_worker":
            return self._handle_seek_worker(directive, intent)

        if intent["intent"] == "start_meeting":
            return self._handle_start_meeting(directive, intent)

        if intent["intent"] == "reply_chat":
            return await self._handle_reply_chat(directive, intent)

        print("[BOSS] 未知意图类型 '%s': %s" % (intent["intent"], directive.text[:60]))
        return []

    async def _classify_boss_intent(self, text: str) -> dict[str, str]:
        """调用 LLM 解析老板指令意图。"""
        try:
            from app.llm_client import llm_client
            result = await llm_client.complete_boss_intent(text)
            print("[BOSS INTENT] 原始返回: %s" % result)
            if result.get("intent"):
                print("[BOSS INTENT] %s → %s (%s)" % (
                    text[:40], result["intent"], result.get("reason", "")[:60]))
                return result
            else:
                print("[BOSS INTENT] LLM 返回了数据但 intent 字段为空或无效")
                return {}
        except Exception as e:
            print("[BOSS INTENT] LLM 调用异常: %s" % e)
            return {}

    def _resolve_worker_id(self, name: str) -> str | None:
        """通过名字或角色匹配 worker_id，大小写不敏感。"""
        if not name:
            return None
        name_lower = name.lower()
        best_match = None
        best_score = 0
        for agent in self.agents.values():
            # 精确名字匹配优先
            if agent.name.lower() == name_lower:
                return agent.worker_id
            # 包含匹配（处理简称）
            score = 0
            if name_lower in agent.name.lower():
                score = len(name)
            if name_lower in agent.role.lower():
                score = max(score, len(name))
            if score > best_score:
                best_score = score
                best_match = agent.worker_id
        return best_match

    def _handle_seek_worker(self, directive: BossDirective, intent: dict) -> list[AgentCommand]:
        """找人指令：让 actor 去找 target 面对面交谈。"""
        actor_id = self._resolve_worker_id(intent.get("actor_name", ""))
        helper_id = self._resolve_worker_id(intent.get("target_name", ""))
        if not actor_id:
            print("[ERRAND] actor 名字解析失败: %s" % intent.get("actor_name", ""))
            return []
        # target 找不到时，随机选一个非 actor 的 agent
        if not helper_id or helper_id == actor_id:
            candidates = [a.worker_id for a in self.agents.values() if a.worker_id != actor_id]
            if candidates:
                import random
                helper_id = random.choice(candidates)
                print("[ERRAND] target '%s' 未匹配，随机选择: %s" % (intent.get("target_name", ""), helper_id))
            else:
                print("[ERRAND] 无可用 target")
                return []
        if self.meeting.locks_worker(actor_id):
            return []
        actor = self.agents.get(actor_id)
        helper = self.agents.get(helper_id)
        if not actor or not helper:
            return []
        actor.interrupt_for_errand(directive, helper_id)
        payload = actor.snapshot().model_dump()
        payload["decision_source"] = "llm_intent"
        payload["errand_target_worker_id"] = helper_id
        payload["errand_target_name"] = helper.name
        payload["errand_directive_text"] = directive.text
        print("[ERRAND] %s(%s) → 找 %s(%s) | 指令: %s" % (
            actor.name, actor_id, helper.name, helper_id, directive.text[:40]))
        return [AgentCommand(
            worker_id=actor.worker_id,
            action="errand_seek",
            say="",
            payload=payload,
        )]

    def _handle_start_meeting(self, directive: BossDirective, intent: dict) -> list[AgentCommand]:
        """开会指令：启动会议流程。"""
        commands = self.meeting.start_meeting(directive)
        for cmd in commands:
            cmd.payload["intent_source"] = "llm_intent"
        return commands

    async def _handle_reply_chat(self, directive: BossDirective, intent: dict) -> list[AgentCommand]:
        """纯聊天指令：调 LLM 生成对方回复，直接显示气泡。携带记忆上下文。"""
        target_id = self._resolve_worker_id(intent.get("target_name", ""))
        # 如果 LLM 没解析出目标名，尝试从 @ 列表取
        if not target_id and directive.target_worker_ids:
            target_id = directive.target_worker_ids[0]
        if not target_id:
            return []

        agent = self.agents.get(target_id)
        if not agent:
            return []

        # 构建记忆上下文：角色档案 + 近期事件
        memory_context = memory_store.build_context(target_id, directive.text)

        # 调 LLM 生成回复（带记忆上下文，使用老板聊天专用 prompt）
        reply_text = ""
        try:
            from app.llm_client import llm_client
            reply_text = await llm_client.generate_boss_reply(
                worker_name=agent.name,
                worker_role=agent.role,
                worker_personality=agent.personality,
                boss_message=directive.text,
                memory_context=memory_context,
            )
        except Exception as e:
            print("[REPLY_CHAT] LLM 调用失败: %s" % e)
            return []

        if not reply_text or len(reply_text.strip()) < 2:
            return []

        # 记录这次对话到记忆
        memory_store.remember(
            target_id,
            "老板直接问：%s → 我回复：%s" % (directive.text[:60], reply_text[:60]),
            kind="boss_chat",
        )

        print("[REPLY_CHAT] %s(%s): %s" % (agent.name, target_id, reply_text[:50]))
        chat_cmd = AgentCommand(
            worker_id=target_id,
            action="chat_line",
            say=reply_text.strip(),
            payload={
                "session_id": "direct",
                "turn": 0,
                "speaker_id": target_id,
                "listener_id": "boss",
                "display_seconds": 4.0,
                "intent_source": "llm_intent",
            },
        )
        idle_cmd = self._idle_command_for(agent)
        return [chat_cmd, idle_cmd]

    # ===== 辅助方法 =====

    def snapshots(self) -> list[AgentSnapshot]:
        return [agent.snapshot() for agent in self.agents.values()]

    def profile_commands(self) -> list[AgentCommand]:
        commands: list[AgentCommand] = []
        for agent in self.agents.values():
            payload = agent.snapshot().model_dump()
            payload["decision_source"] = "profile_update"
            commands.append(AgentCommand(
                worker_id=agent.worker_id,
                action="idle",
                say="",
                payload=payload,
            ))
        return commands

    def company_snapshot(self) -> CompanySnapshot:
        return self.company.snapshot(self.snapshots())

    def task_snapshots(self) -> list[ProjectTaskSnapshot]:
        return [task.snapshot() for task in self.company.tasks.values()]

    def _reset_runtime_for_new_scene(self) -> None:
        self.company = CompanyProject()
        self.planning = ProjectPlanningService(self.company, self.agents)
        self.meeting.update_context(self.company, self.targets, self.planning)
        self.meeting.clear()
        self.directives.clear()
        office_clock.reset()
        self._a2a_manager = A2ASessionManager()
        for agent in self.agents.values():
            agent.reset_runtime_state()

    def _get_or_create_agent(self, worker_id: str) -> OfficeAgent:
        agent = self.agents.get(worker_id)
        if agent is None:
            agent = make_agent(
                worker_id, worker_id, "临时员工",
                "普通，正在适应团队节奏", "根据现场情况行动",
                "谨慎、先确认再行动",
                ["明确任务", "减少返工"],
                ["上下文不足", "目标频繁变化"],
            )
            self.agents[worker_id] = agent
        return agent

    def _update_targets(self, event: WorkerEvent) -> None:
        seats: list[str] = []
        idle_points: list[str] = []
        roam_points: list[str] = []
        for item in event.payload.get("targets", []):
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("id", ""))
            group = str(item.get("group", ""))
            if not target_id:
                continue
            if group == "seat_markers":
                seats.append(target_id)
            elif group == "idle_markers":
                idle_points.append(target_id)
            elif group == "supMarkers":
                roam_points.append(target_id)
        self.targets = OfficeTargets(seats=seats, idle_points=idle_points, roam_points=roam_points)

    async def _handle_task_progress(self, event: WorkerEvent) -> list[AgentCommand]:
        """处理前端发来的任务进度更新。
        用 LLM function calling 生成实时工作状态文本，同时推送 task_update 到手机面板。"""
        payload = event.payload or {}
        task_id = str(payload.get("task_id", ""))
        delta = float(payload.get("progress_delta", 0.0))
        worker_id = event.worker_id

        agent = self.agents.get(worker_id)
        if not agent or not task_id:
            return []

        task = self.company.tasks.get(task_id)
        if not task:
            return []

        # 任务已结束（done/review）：清空 worker 的任务绑定，不再推进
        if task.status in ("done", "review"):
            if agent.active_task_id == task_id:
                agent.active_task_id = ""
                agent.status = "在办公室待命" if task.status == "done" else "等待审核"
                agent.focus_task = "等待下一个任务"
            commands: list[AgentCommand] = []
            commands.append(AgentCommand(
                worker_id=worker_id,
                action="idle",
                say="",
                payload=agent.snapshot().model_dump(),
            ))
            commands.append(AgentCommand(
                worker_id="office",
                action="task_update",
                say="",
                payload={"tasks": [t.snapshot() for t in self.company.tasks.values()]},
            ))
            return commands

        # 推进任务进度
        completed = self.company.advance_task(task_id, agent.name, delta)
        if completed:
            agent.completed_task_count += 1

        # 任务刚进入 review/done：清任务绑定，跳过 LLM 调用
        if task.status in ("done", "review"):
            if agent.active_task_id == task_id:
                agent.active_task_id = ""
                agent.status = "在办公室待命" if task.status == "done" else "等待审核"
                agent.focus_task = "等待下一个任务"
            commands: list[AgentCommand] = []
            commands.append(AgentCommand(
                worker_id=worker_id,
                action="idle",
                say="",
                payload=agent.snapshot().model_dump(),
            ))
            commands.append(AgentCommand(
                worker_id="office",
                action="task_update",
                say="",
                payload={"tasks": [t.snapshot() for t in self.company.tasks.values()]},
            ))
            return commands

        # 用 LLM function calling 生成工作状态文本（缓存：跨过 15% 门槛才重新调）
        cache_key = worker_id
        cached = self._work_status_cache.get(cache_key)
        threshold = int(task.progress * 100) // 15
        if cached is None or cached["threshold"] != threshold:
            status_text = await llm_client.generate_work_update(
                name=agent.name,
                role=agent.role,
                task_title=task.title,
                progress_pct=task.progress,
                last_status=cached["text"] if cached else "",
            )
            # LLM 返回空（限流/异常）时不覆盖缓存，保留上次有效文本
            if status_text:
                self._work_status_cache[cache_key] = {"text": status_text, "threshold": threshold}
            elif cached:
                status_text = cached["text"]
        else:
            status_text = cached["text"]

        # LLM 始终无结果时，用任务标题做 fallback
        if not status_text:
            status_text = task.title[:20]

        # 更新 agent 状态（任务进行中）
        if status_text:
            agent.status = agent.focus_task if task.progress >= 0.8 else status_text
            agent.focus_task = task.title

        commands: list[AgentCommand] = []
        # idle 命令：头顶气泡显示工作状态
        commands.append(AgentCommand(
            worker_id=worker_id,
            action="idle",
            say=("%s：%s" % (agent.name, status_text)) if status_text else "",
            payload=agent.snapshot().model_dump(),
        ))
        # 推送 task_update 到手机面板
        commands.append(AgentCommand(
            worker_id="office",
            action="task_update",
            say="",
            payload={"tasks": [t.snapshot() for t in self.company.tasks.values()]},
        ))
        return commands


office_runtime = OfficeRuntime()
