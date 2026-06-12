from time import time

import asyncio

from app.domain import BossDirective, CompanyProject, OfficeTargets
from app.meeting_autogen import MeetingParticipant, run_round_robin_meeting
from app.meeting_session import MeetingSession
from app.planning_service import ProjectPlanningService
from app.schemas import AgentCommand, WorkerEvent
from app.worker_agent import OfficeAgent
from app.worker_llm_decision import clean_visible_text


class MeetingRuntime:
    """会议模式运行器：创建会议、锁定参会人、播放发言、会议结束后派工。"""

    def __init__(self, company: CompanyProject, targets: OfficeTargets, agents: dict[str, OfficeAgent], planning: ProjectPlanningService) -> None:
        self.company = company
        self.targets = targets
        self.agents = agents
        self.planning = planning
        self.active_session: MeetingSession | None = None

    def update_context(self, company: CompanyProject, targets: OfficeTargets, planning: ProjectPlanningService) -> None:
        self.company = company
        self.targets = targets
        self.planning = planning

    def clear(self) -> None:
        if self.active_session and self.active_session.turns_task:
            task = self.active_session.turns_task
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        self.active_session = None

    def start_meeting(self, directive: BossDirective) -> list[AgentCommand]:
        directive.target_worker_ids = self._select_meeting_workers(directive)
        self._assign_meeting_seats(directive.target_worker_ids)
        self.active_session = self._create_meeting_session(directive)
        self._prefetch_meeting_turns(self.active_session)
        return self._meeting_move_commands(self.active_session)

    async def handle_event(self, event: WorkerEvent) -> list[AgentCommand]:
        return await self._handle_meeting_event(event)

    def consumes_event(self, event: WorkerEvent) -> bool:
        return self._meeting_consumes_event(event)

    def is_meeting_event(self, event: WorkerEvent) -> bool:
        return event.type in ["worker_arrived", "meeting_say_done"]

    def locks_worker(self, worker_id: str) -> bool:
        session = self.active_session
        return bool(session and worker_id in session.participant_ids)

    def wait_command(self, worker_id: str) -> AgentCommand:
        return self._meeting_wait_command(worker_id)

    def locked_worker_command(self, event: WorkerEvent) -> AgentCommand:
        """会议期间参会人只允许等候或回到自己的会议座位。"""
        session = self.active_session
        if session is None:
            return self._meeting_wait_command(event.worker_id)
        seat_id = session.seat_for(event.worker_id)
        if seat_id and event.worker_id not in session.seated_worker_ids:
            return self._meeting_reseat_command(event.worker_id, seat_id, session)
        return self._meeting_wait_command(event.worker_id)
    def _create_meeting_session(self, directive: BossDirective) -> MeetingSession:
        seats_by_worker: dict[str, str] = {}
        for worker_id in directive.target_worker_ids[:8]:
            agent = self.agents.get(worker_id)
            if agent and agent.assigned_meeting_seat:
                seats_by_worker[worker_id] = agent.assigned_meeting_seat
                agent.current_directive = directive.text
                agent.focus_task = directive.text
                agent.status = "去会议室"
                agent.mood = "准备讨论"
        return MeetingSession(
            session_id=f"meeting-{int(time())}",
            topic=directive.text,
            participant_ids=list(seats_by_worker.keys()),
            seats_by_worker=seats_by_worker,
            max_turns=min(12, max(6, len(seats_by_worker) + 2)),
        )
    
    def _meeting_move_commands(self, session: MeetingSession) -> list[AgentCommand]:
        commands: list[AgentCommand] = []
        for worker_id in session.participant_ids:
            agent = self.agents[worker_id]
            payload = agent.snapshot().model_dump()
            payload["travel_mode"] = "meeting"
            payload["meeting_session_id"] = session.session_id
            payload["meeting_topic"] = session.topic
            payload["decision_source"] = "meeting_session"
            payload["work_context"] = {
                "intent": "先到会议室入座，等待统一会议轮次",
                "work_update": f"准备参加会议：{session.topic}",
                "risk_note": "",
                "needs_help_from": "",
                "confirmation_question": "",
                "confidence": 0.9,
            }
            commands.append(AgentCommand(
                worker_id=worker_id,
                action="move_to",
                target_id=session.seat_for(worker_id),
                say="",
                payload=payload,
            ))
        return commands
    
    async def _handle_meeting_event(self, event: WorkerEvent) -> list[AgentCommand]:
        session = self.active_session
        if session is None:
            return []
    
        if event.type == "worker_arrived":
            session.mark_arrived(event.worker_id, event.target_id or "")
            if session.is_ready() and not session.is_started:
                session.is_started = True
                session.pending_turns = await self._get_prefetched_meeting_turns(session)
                return [await self._next_meeting_say_command(session)]
            return []
    
        if event.type == "meeting_say_done" and session.is_started:
            if session.has_more_turns():
                return [await self._next_meeting_say_command(session)]
            return await self._finish_meeting(session)
    
        return []
    
    def _meeting_consumes_event(self, event: WorkerEvent) -> bool:
        session = self.active_session
        if session is None:
            return False
        if event.type == "meeting_say_done":
            return True
        if event.type != "worker_arrived":
            return False
        return event.worker_id in session.participant_ids and event.target_id == session.seat_for(event.worker_id)
    
    def _is_meeting_event(self, event: WorkerEvent) -> bool:
        return event.type in ["worker_arrived", "meeting_say_done"]
    
    def _is_worker_locked_in_meeting(self, worker_id: str) -> bool:
        session = self.active_session
        return bool(session and worker_id in session.participant_ids)
    
    def _meeting_wait_command(self, worker_id: str) -> AgentCommand:
        agent = self.agents.get(worker_id)
        payload = agent.snapshot().model_dump() if agent else {}
        payload["decision_source"] = "meeting_lock"
        payload["travel_mode"] = "meeting"
        return AgentCommand(worker_id=worker_id, action="idle", say="", payload=payload)

    def _meeting_reseat_command(self, worker_id: str, seat_id: str, session: MeetingSession) -> AgentCommand:
        agent = self.agents.get(worker_id)
        payload = agent.snapshot().model_dump() if agent else {}
        payload["decision_source"] = "meeting_reseat"
        payload["travel_mode"] = "meeting"
        payload["meeting_session_id"] = session.session_id
        payload["meeting_topic"] = session.topic
        return AgentCommand(worker_id=worker_id, action="move_to", target_id=seat_id, say="", payload=payload)
    
    async def _next_meeting_say_command(self, session: MeetingSession) -> AgentCommand:
        turn = session.pop_turn()
        if not turn:
            return AgentCommand(worker_id="office", action="idle", say="会议暂时没有新的发言。")
        speaker_id = str(turn.get("worker_id", ""))
        agent = self.agents[speaker_id]
        reply = str(turn.get("text", ""))
        reply = clean_visible_text(reply)
        agent.status = "会议发言"
        agent.focus_task = session.topic
        payload = agent.snapshot().model_dump()
        payload["display"] = "speech"
        payload["meeting_session_id"] = session.session_id
        payload["meeting_topic"] = session.topic
        payload["meeting_turn"] = len(session.transcript)
        payload["meeting_transcript"] = session.transcript[-8:]
        return AgentCommand(
            worker_id=speaker_id,
            action="say",
            say=f"{agent.name}：{reply}",
            payload=payload,
        )
    
    async def _finish_meeting(self, session: MeetingSession) -> list[AgentCommand]:
        directive = BossDirective(
            text=session.topic,
            priority=3,
            target_worker_ids=session.participant_ids,
        )
        assigned_tasks = await self.planning.create_directive_tasks(directive)
        commands: list[AgentCommand] = []
        for worker_id in session.participant_ids:
            agent = self.agents.get(worker_id)
            if agent is None:
                continue
            task = assigned_tasks.get(worker_id)
            if task:
                agent.apply_directive(directive, task)
            agent.assigned_meeting_seat = ""
            agent.status = "会议结束，回工位开工"
            agent.mood = "明确下一步"
            desk_id = self.targets.own_desk(worker_id)
            if not desk_id:
                continue
            payload = agent.snapshot().model_dump()
            payload["travel_mode"] = "normal"
            payload["decision_source"] = "meeting_finished"
            payload["meeting_session_id"] = session.session_id
            payload["meeting_topic"] = session.topic
            payload["work_context"] = {
                "intent": "会议结束，回本人固定工位推进分配任务",
                "work_update": task.title if task else f"整理会议结论：{session.topic}",
                "risk_note": "",
                "needs_help_from": "",
                "confirmation_question": "",
                "confidence": 0.9,
            }
            commands.append(AgentCommand(
                worker_id=worker_id,
                action="move_to",
                target_id=desk_id,
                say=f"{agent.name}：会议结束，我回工位整理任务。",
                payload=payload,
            ))
        self.active_session = None
        return commands

    def _prefetch_meeting_turns(self, session: MeetingSession) -> None:
        """员工走向会议室时就开始请求会议文本，入座后尽量直接播放。"""
        if session.turns_task:
            return
        try:
            session.turns_task = asyncio.create_task(self._build_meeting_turns(session))
        except RuntimeError:
            session.turns_task = None

    async def _get_prefetched_meeting_turns(self, session: MeetingSession) -> list[dict[str, str]]:
        task = session.turns_task
        if isinstance(task, asyncio.Task):
            try:
                turns = await task
            except Exception:
                turns = []
            if turns:
                return turns
        return await self._build_meeting_turns(session)
    
    async def _build_meeting_turns(self, session: MeetingSession) -> list[dict[str, str]]:
        participants = [
            MeetingParticipant(
                worker_id=worker_id,
                name=self.agents[worker_id].name,
                role=self.agents[worker_id].role,
                prompt=self.agents[worker_id].roleplay_prompt(),
            )
            for worker_id in session.participant_ids
            if worker_id in self.agents
        ]
        try:
            turns = await run_round_robin_meeting(
                topic=session.topic,
                participants=participants,
                max_turns=session.max_turns,
            )
        except Exception:
            turns = []
        if turns:
            return turns
        return [
            {
                "worker_id": worker_id,
                "speaker": self.agents[worker_id].name,
                "text": self._fallback_meeting_reply(self.agents[worker_id], session),
            }
            for worker_id in session.participant_ids
            if worker_id in self.agents
        ][: session.max_turns]
    
    def _fallback_meeting_reply(self, agent: OfficeAgent, session: MeetingSession) -> str:
        topic = self._meeting_topic_label(session.topic)
        if "产品" in agent.role:
            return f"我先把{topic}的用户场景和验收口径列出来。"
        if "项目经理" in agent.role:
            return f"先定目标、负责人和时间点，别一上来就散。"
        if "后端" in agent.role or "架构" in agent.role:
            return f"我关注接口边界、数据结构和上线风险。"
        if "前端" in agent.role or "UI" in agent.role:
            return f"我需要明确页面状态和交互流程。"
        if "测试" in agent.role:
            return f"我会补复现路径、验收标准和回归范围。"
        if "数据" in agent.role:
            return f"要先定义指标，不然上线后没法判断效果。"
        return f"我先听大家结论，再补我这边能做的事。"
    
    def _meeting_topic_label(self, topic: str) -> str:
        cleaned = topic
        for word in ["去会议室", "开会", "会议", "讨论一下", "讨论", "聊一下", "碰一下", "问题"]:
            cleaned = cleaned.replace(word, "")
        cleaned = cleaned.strip(" ：，。")
        return cleaned or "这个项目"
    
    def _select_meeting_workers(self, directive: BossDirective) -> list[str]:
        if directive.target_worker_ids:
            return directive.target_worker_ids[:8]
    
        role_priority = [
            "worker1",  # 项目经理
            "worker3",  # 产品
            "worker2",  # 后端
            "worker9",  # 前端
            "worker5",  # 设计
            "worker6",  # 测试
            "worker4",  # 架构
            "worker8",  # 数据
            "worker7",
            "worker11",
            "worker10",
        ]
        task_type = self.company._infer_task_type(directive.text)
        if task_type == "ops":
            role_priority = ["worker1", "worker3", "worker7", "worker6", "worker2", "worker9", "worker4", "worker8", "worker5", "worker11", "worker10"]
        elif task_type == "design":
            role_priority = ["worker1", "worker3", "worker5", "worker9", "worker2", "worker6", "worker4", "worker8", "worker7", "worker11", "worker10"]
        elif task_type == "backend":
            role_priority = ["worker1", "worker3", "worker2", "worker4", "worker9", "worker6", "worker5", "worker8", "worker7", "worker11", "worker10"]
    
        selected: list[str] = []
        for worker_id in role_priority:
            if worker_id in self.agents and worker_id not in selected:
                selected.append(worker_id)
            if len(selected) >= 8:
                break
        return selected
    
    def _assign_meeting_seats(self, worker_ids: list[str]) -> None:
        meeting_seats = self.targets.meeting_seats()
        preferred_order = [
            "leftTopChair",
            "rightTopChair",
            "leftUpperChair",
            "rightUpperChair",
            "leftLowerChair",
            "rightLowerChair",
            "leftBottomChair",
            "rightBottomChair",
        ]
        ordered_seats = [seat for seat in preferred_order if seat in meeting_seats]
        ordered_seats += [seat for seat in meeting_seats if seat not in ordered_seats]
        for agent in self.agents.values():
            agent.assigned_meeting_seat = ""
        for index, worker_id in enumerate(worker_ids[:8]):
            if worker_id in self.agents and index < len(ordered_seats):
                self.agents[worker_id].assigned_meeting_seat = ordered_seats[index]
    
    
    
