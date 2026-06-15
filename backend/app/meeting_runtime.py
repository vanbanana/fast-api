from time import time

import asyncio
import logging

from app.domain import BossDirective, CompanyProject, OfficeTargets
from app.llm_client import llm_client
from app.meeting_session import MeetingSession, MeetingPhase
from app.planning_service import ProjectPlanningService
from app.schemas import AgentCommand, WorkerEvent
from app.worker_agent import OfficeAgent
from app.worker_llm_decision import clean_visible_text

logger = logging.getLogger(__name__)


class MeetingRuntime:
    """会议模式运行器：创建会议、锁定参会人、播放发言、会议结束后派工。"""

    def __init__(self, company: CompanyProject, targets: OfficeTargets, agents: dict[str, OfficeAgent], planning: ProjectPlanningService) -> None:
        self.company = company
        self.targets = targets
        self.agents = agents
        self.planning = planning
        self.active_session: MeetingSession | None = None
        # 会议结束后暂存 task_update，等有人回到工位再推送
        self._pending_task_update: AgentCommand | None = None

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
        self._pending_task_update = None

    def consume_pending_task_update(self, worker_id: str) -> AgentCommand | None:
        """会议结束后，第一个回到工位的人触发 task_update 推送。"""
        if self._pending_task_update is None:
            return None
        cmd = self._pending_task_update
        self._pending_task_update = None
        return cmd

    def start_meeting(self, directive: BossDirective) -> list[AgentCommand]:
        directive.target_worker_ids = self._select_meeting_workers(directive)
        self._assign_meeting_seats(directive.target_worker_ids)
        self.active_session = self._create_meeting_session(directive)
        # 保存参会人当前工作状态（中断恢复）
        for worker_id in directive.target_worker_ids[:8]:
            agent = self.agents.get(worker_id)
            if agent and agent.active_task_id:
                agent.interrupted_task_id = agent.active_task_id
                agent.interrupted_focus_task = agent.focus_task
                agent.active_task_id = ""
        return self._meeting_move_commands(self.active_session)

    async def handle_event(self, event: WorkerEvent) -> list[AgentCommand]:
        return await self._handle_meeting_event(event)

    def consumes_event(self, event: WorkerEvent) -> bool:
        return self._meeting_consumes_event(event)

    def is_meeting_event(self, event: WorkerEvent) -> bool:
        return event.type in ["worker_arrived", "meeting_say_done"]

    def locks_worker(self, worker_id: str) -> bool:
        session = self.active_session
        return bool(session and not session.is_finished() and worker_id in session.participant_ids)

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

        # 主管默认为第一个参会人（通常是 worker1/项目经理）
        lead_id = list(seats_by_worker.keys())[0] if seats_by_worker else ""

        return MeetingSession(
            session_id=f"meeting-{int(time())}",
            topic=directive.text,
            participant_ids=list(seats_by_worker.keys()),
            seats_by_worker=seats_by_worker,
            lead_worker_id=lead_id,
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

        # --- 阶段 1：有人到达座位 ---
        if event.type == "worker_arrived":
            session.mark_arrived(event.worker_id, event.target_id or "")
            if session.is_ready() and not session.is_discussing():
                # 所有人到齐 → 进入讨论阶段，生成第一轮发言
                session.start_discussion()
                return [await self._generate_next_turn(session)]
            # 还没到齐：给已到达的人发"已落座"状态
            return [self._seated_status_command(event.worker_id, session)]

        # --- 阶段 2：一轮发言结束 ---
        if event.type == "meeting_say_done":
            # === 关闭阶段：主管先出PRD，再等大家确认 ===
            if session.is_closing():
                # === 主管 PRD 两阶段加载 ===
                if not session.closing_prd_said:
                    if session.lead_prd_pending:
                        # Phase 2: 主管刚说完"正在整理..." → 调用 LLM 生成 PRD
                        if event.worker_id == session.lead_worker_id:
                            return [await self._generate_next_ack(session, session.lead_worker_id)]
                        return [self._meeting_wait_command(event.worker_id)]
                    else:
                        # Phase 1: 主管刚开始 → 先显示"正在整理..." 不调 LLM
                        if event.worker_id == session.lead_worker_id:
                            return [await self._generate_next_ack(session, session.lead_worker_id)]
                        return [self._meeting_wait_command(event.worker_id)]

                # === 主管 PRD 已出 → 让其他人回复"收到" ===
                if event.worker_id != session.lead_worker_id:
                    session.mark_acknowledged(event.worker_id)
                if session.all_acknowledged():
                    return await self._finish_meeting(session)
                pending = session.pending_ack_ids()
                if pending:
                    return [await self._generate_next_ack(session, pending[0])]
                return await self._finish_meeting(session)

            # === 讨论阶段：正常轮转 ===
            if session.is_discussing():
                if session.has_more_turns() and not session.is_finished():
                    return [await self._generate_next_turn(session)]
                # 超过 max_turns → 进入关闭阶段（不是直接结束！让主管出PRD）
                logger.info("[MEETING] 达到讨论轮次上限(%d)，进入PRD总结阶段", session.max_turns)
                session.start_closing()
                # 主管输出 PRD 总结
                lead_id = session.lead_worker_id
                if lead_id:
                    return [await self._generate_next_ack(session, lead_id)]
                return await self._finish_meeting(session)

        return []

    def _seated_status_command(self, worker_id: str, session: MeetingSession) -> AgentCommand:
        """已落座状态命令（气泡显示 + 状态更新）。"""
        agent = self.agents.get(worker_id)
        if agent:
            agent.status = "已落座"
        payload = (agent.snapshot().model_dump() if agent else {})
        payload["decision_source"] = "meeting_seated"
        payload["travel_mode"] = "meeting"
        payload["meeting_session_id"] = session.session_id
        payload["meeting_topic"] = session.topic
        name = agent.name if agent else worker_id
        return AgentCommand(
            worker_id=worker_id,
            action="status",
            say="",
            status="已落座",
            display_name=name,
            payload=payload,
        )
    
    def _meeting_consumes_event(self, event: WorkerEvent) -> bool:
        session = self.active_session
        if session is None:
            return False
        if event.type == "meeting_say_done":
            return True
        if event.type != "worker_arrived":
            return False
        return event.worker_id in session.participant_ids and event.target_id == session.seat_for(event.worker_id)
    
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
    
    async def _generate_next_turn(self, session: MeetingSession) -> AgentCommand:
        """逐轮调用 LLM 生成会议发言（function calling）。

        如果 LLM 返回空，自动跳到下一个人重试（最多跳过全员一轮）。
        连续全部空回复则强制结束会议。
        """
        max_retries = len(session.participant_ids) * 2  # 最多绕两圈
        for _ in range(max_retries):
            speaker_id = session.next_speaker_id()
            agent = self.agents.get(speaker_id)
            if not agent:
                session._bump_turn()  # 跳过无效 speaker
                continue

            roster = "、".join(
                "%s/%s" % (self.agents[wid].name, self.agents[wid].role)
                for wid in session.participant_ids if wid in self.agents
            )

            reply, done, prd_point = await llm_client.generate_meeting_turn(
                speaker_name=agent.name,
                speaker_role=agent.role,
                topic=session.topic,
                roster=roster,
                transcript_summary=session.get_transcript_summary(),
                turn_number=session.current_turn,
                is_lead=session.is_lead(speaker_id),
            )

            if not reply:
                # LLM 空回复 → 跳过这个人，继续下一位
                session._bump_turn()
                logger.warning("[MEETING] LLM 空回复，跳过 %s (turn=%d/%d)",
                               agent.name, session.current_turn, session.max_turns)
                continue

            # 记录本轮发言
            session.record_turn(speaker_id, agent.name, reply, done, prd_point)

            # 如果主管提议结束 → 进入关闭阶段（不直接结束！）
            if done and session.is_lead(speaker_id):
                session.start_closing()
                logger.info("[MEETING] 主管 %s 提议结束，进入PRD总结阶段",
                            agent.name)

            reply = clean_visible_text(reply)
            # 关闭阶段：主管的发言要带上完整 PRD 总结
            if session.is_closing() and session.is_lead(speaker_id) and session.prd_final:
                closing_suffix = "\n【会议结论/任务分配】%s" % (session.prd_final[:150],)
                if closing_suffix not in reply:
                    reply = reply + closing_suffix
                agent.status = "总结会议结论"
            else:
                agent.status = ""  # 清掉"已落座"等旧状态
            agent.focus_task = session.topic
            payload = agent.snapshot().model_dump()
            payload["display"] = "speech"
            payload["meeting_session_id"] = session.session_id
            payload["meeting_topic"] = session.topic
            payload["meeting_turn"] = session.current_turn

            return AgentCommand(
                worker_id=speaker_id,
                action="say",
                say="%s：%s" % (agent.name, reply),
                payload=payload,
            )

        # 全部重试都失败 → 强制结束会议
        logger.warning("[MEETING] 连续 %d 次空回复，强制结束会议", max_retries)
        session.finish()
        return await self._finish_meeting(session)

    async def _generate_next_ack(self, session: MeetingSession, ack_worker_id: str) -> AgentCommand:
        """关闭阶段：生成参会人的回复。主管输出PRD总结，其他人回复'收到'。"""
        agent = self.agents.get(ack_worker_id)
        if not agent:
            session.mark_acknowledged(ack_worker_id)
            pending = session.pending_ack_ids()
            if pending:
                return await self._generate_next_ack(session, pending[0])
            return await self._finish_meeting(session)

        # === 主管：两阶段 PRD 生成 ===
        if ack_worker_id == session.lead_worker_id and not session.closing_prd_said:
            if not session.lead_prd_pending:
                # Phase 1: 显示"正在整理..."，不调 LLM，等前端播完再回来
                session.lead_prd_pending = True
                agent.status = "整理内容中..."
                payload = agent.snapshot().model_dump()
                payload["display"] = "speech"
                payload["meeting_session_id"] = session.session_id
                payload["meeting_topic"] = session.topic
                logger.info("[MEETING] %s 开始整理会议结论...", agent.name)
                return AgentCommand(
                    worker_id=ack_worker_id,
                    action="say",
                    say="%s：正在整理会议结论..." % agent.name,
                    payload=payload,
                )
            else:
                # Phase 2: 调用 LLM 生成详细 PRD
                logger.info("[MEETING] %s 调用LLM生成PRD总结...", agent.name)
                prd_text = await llm_client.generate_meeting_prd_summary(
                    lead_name=agent.name,
                    topic=session.topic,
                    full_transcript="\n".join(
                        "%s: %s" % (m["speaker"], m["text"])
                        for m in session.transcript
                    ),
                )
                if prd_text:
                    session.prd_final = prd_text
                else:
                    session.prd_final = "讨论结论：%s" % session.topic
                session.closing_prd_said = True
                session.lead_prd_pending = False
                agent.status = "总结完成"
                payload = agent.snapshot().model_dump()
                payload["display"] = "speech"
                payload["meeting_session_id"] = session.session_id
                payload["meeting_topic"] = session.topic

                logger.info("[MEETING] %s 输出PRD总结(%d字)", agent.name, len(session.prd_final) if session.prd_final else 0)

                return AgentCommand(
                    worker_id=ack_worker_id,
                    action="say",
                    say="%s：好，大家推进吧，PRD已同步到任务看板。" % agent.name,
                    payload=payload,
                )

        # === 其他员工：回复"收到" ===
        import random
        ack_phrases = ["收到", "好的", "明白", "没问题，我去推进"]
        ack_text = random.choice(ack_phrases)
        session.mark_acknowledged(ack_worker_id)
        agent.status = "确认收到任务"
        payload = agent.snapshot().model_dump()
        payload["display"] = "speech"
        payload["meeting_session_id"] = session.session_id
        payload["meeting_topic"] = session.topic

        logger.info("[MEETING] %s: %s (收到确认 %d/%d)",
                    agent.name, ack_text,
                    len(session.acknowledged_ids),
                    len(session.participant_ids) - 1)

        return AgentCommand(
            worker_id=ack_worker_id,
            action="say",
            say="%s：%s" % (agent.name, ack_text),
            payload=payload,
        )

    def _empty_turn_command(self, session: MeetingSession) -> AgentCommand:
        """LLM 失败时的空命令。"""
        return AgentCommand(worker_id="office", action="idle", say="")
    
    async def _finish_meeting(self, session: MeetingSession) -> list[AgentCommand]:
        session.finish()
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
            # 恢复之前被中断的任务（如果有）
            if agent.interrupted_task_id and not task:
                agent.active_task_id = agent.interrupted_task_id
                agent.focus_task = agent.interrupted_focus_task or agent.focus_task
                agent.status = "回到原任务继续"
                agent.remember(f"会议结束，恢复原任务:{agent.focus_task}")
            elif task:
                agent.status = "执行会议分配的新任务"
            agent.interrupted_task_id = ""
            agent.interrupted_focus_task = ""
            agent.assigned_meeting_seat = ""
            # 确定回工位后的状态文字（前端据此判断是否显示绿色）
            if task:
                agent.status = "工作中"
                agent.focus_task = task.title
            elif agent.active_task_id:
                agent.status = "工作中"
            else:
                agent.status = "会议结束，回工位开工"
            agent.mood = "明确下一步"
            desk_id = self.targets.own_desk(worker_id)
            if not desk_id:
                continue
            payload = agent.snapshot().model_dump()
            payload["travel_mode"] = "meeting_finished"
            payload["decision_source"] = "meeting_finished"
            payload["meeting_session_id"] = session.session_id
            payload["meeting_topic"] = session.topic
            # 附带 PRD 总结（如果有）
            if session.prd_final:
                payload["prd_summary"] = session.prd_final
            payload["work_context"] = {
                "intent": "会议结束，回本人固定工位开始工作",
                "work_update": task.title if task else f"推进任务：{session.topic}",
                "risk_note": "",
                "needs_help_from": "",
                "confirmation_question": "",
                "confidence": 0.9,
            }
            # 主管简洁收尾，其他人按任务回复
            if session.prd_final and worker_id == session.lead_worker_id:
                say_text = "%s：好，大家回去推进吧，PRD已同步到任务看板。" % (agent.name,)
            elif task:
                say_text = "%s：收到，我去搞%s。" % (agent.name, task.title[:20])
            else:
                say_text = "%s：好，回工位开工。" % (agent.name,)
            commands.append(AgentCommand(
                worker_id=worker_id,
                action="move_to",
                target_id=desk_id,
                say=say_text,
                payload=payload,
            ))
        self.active_session = None
        # 暂存 task_update，等人回到工位再推送（不在 move_to 同一批发）
        self._pending_task_update = AgentCommand(
            worker_id="office",
            action="task_update",
            say="",
            payload={
                "tasks": [t.snapshot() for t in self.company.tasks.values()],
                "prd_summary": session.prd_final or "",
                "meeting_topic": session.topic,
            },
        )
        return commands

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
    
    
    
