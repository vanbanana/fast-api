import random
from dataclasses import dataclass, field

from app.config import settings
from app.domain import BossDirective, CompanyProject, OfficeTargets, ProjectTask
from app.llm_client import llm_client
from app.memory import memory_store
from app.schemas import AgentCommand, AgentSnapshot, WorkerEvent
from app.worker_behavior_tree import STATE_BREAK, choose_rule_behavior, status_for_behavior_state
from app.worker_decision_policy import enforce_fixed_workstation_target, llm_work_targets, normalize_colleague_id, worker_id_from_desk_marker
from app.worker_llm_decision import agent_stream_lines, build_agent_decision_messages, clean_visible_text, normalize_llm_decision, safe_confidence, text_value
from app.worker_rule_context import build_confirmation_question, build_rule_work_context, should_confirm_with_team

@dataclass
class OfficeAgent:
    """单个员工，拥有独立人设、职业职责、短期记忆和自主循环计数。"""

    worker_id: str
    name: str
    role: str
    personality: str
    work_style: str
    communication_style: str
    work_values: list[str]
    conflict_triggers: list[str]
    relationship_notes: dict[str, str]
    roleplay_template: str = ""
    status: str = "观察办公室"
    mood: str = "平稳"
    energy: float = 1.0
    focus_task: str = "熟悉今天的工作"
    current_directive: str = ""
    last_target_id: str = ""
    autonomy_steps: int = 0
    cooldown_steps: int = 0
    active_task_id: str = ""
    completed_task_count: int = 0
    stress: float = 0.18
    needs_help_from: str = ""
    current_risk: str = ""
    confirmation_question: str = ""
    seeking_helper_id: str = ""
    checked_helper_desk: bool = False
    assigned_meeting_seat: str = ""
    pending_interaction_from: str = ""
    pending_interaction_say: str = ""
    pending_interaction_context: dict[str, object] = field(default_factory=dict)
    memory: list[str] = field(default_factory=list)

    def remember(self, text: str) -> None:
        if not memory_store.should_store(text):
            return
        if text in self.memory[-10:]:
            return
        self.memory.append(text)
        self.memory = self.memory[-settings.agent_memory_limit :]
        memory_store.remember(self.worker_id, text)

    def reset_runtime_state(self) -> None:
        """新一局或 Godot 重新同步场景时清理运行态，长期记忆文件不受影响。"""
        self.status = "在办公室待命"
        self.mood = "平稳"
        self.focus_task = "等待明确目标"
        self.current_directive = ""
        self.last_target_id = ""
        self.autonomy_steps = 0
        self.cooldown_steps = 0
        self.active_task_id = ""
        self.needs_help_from = ""
        self.current_risk = ""
        self.confirmation_question = ""
        self.seeking_helper_id = ""
        self.checked_helper_desk = False
        self.assigned_meeting_seat = ""
        self.pending_interaction_from = ""
        self.pending_interaction_say = ""
        self.pending_interaction_context = {}

    def apply_directive(self, directive: BossDirective, task: ProjectTask | None = None) -> None:
        self.current_directive = directive.text
        self.focus_task = directive.text
        if task:
            self.active_task_id = task.task_id
            if should_confirm_with_team(task):
                self.confirmation_question = build_confirmation_question(task)
        self.status = "执行老板指令"
        self.mood = "被关注"
        self.stress = min(1.0, self.stress + 0.04 * directive.priority)
        self.autonomy_steps = 0
        self.cooldown_steps = 0
        self.remember(f"老板指令:{directive.text}")

    async def decide(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject, agents: dict[str, "OfficeAgent"] | None = None) -> AgentCommand:
        self._record_event(event)
        pending_command = self._consume_pending_interaction()
        if pending_command:
            return pending_command
        find_command = await self._continue_find_person_flow(event, targets, agents or {})
        if find_command:
            return find_command
        self._sync_task_focus(company)
        self._advance_work_if_possible(event, targets, company)
        reaction_command = self._react_to_current_place(event, targets, company)
        if reaction_command:
            return reaction_command
        if not targets.all_targets():
            return self._idle_command("还没有同步场景目标。")

        if self.autonomy_steps >= settings.max_autonomy_steps:
            self.cooldown_steps = settings.loop_cooldown_steps
            self.autonomy_steps = 0
            return self._idle_command("自主循环达到上限，短暂整理状态。")

        if self.cooldown_steps > 0:
            self.cooldown_steps -= 1
            return self._idle_command("整理记忆和任务优先级。")

        self.autonomy_steps += 1
        if event.type == "boss_command":
            return self._decide_with_rules(targets, company)
        if not self.current_directive and not company.task_for_agent(self.worker_id):
            return self._decide_with_rules(targets, company)
        if random.random() <= settings.llm_decision_chance:
            llm_command = await self._decide_with_llm(event, targets, company, agents or {})
            if llm_command:
                return llm_command

        return self._decide_with_rules(targets, company)

    def _record_event(self, event: WorkerEvent) -> None:
        self.last_target_id = event.target_id or self.last_target_id
        if event.type == "worker_arrived":
            self.energy = max(0.1, self.energy - 0.015)
            self.status = f"到达 {event.target_id}"

    async def _decide_with_llm(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject, agents: dict[str, "OfficeAgent"]) -> AgentCommand | None:
        active_task = company.tasks.get(self.active_task_id)
        system, user = build_agent_decision_messages(self, event, targets, company, active_task)
        wants_break = self._should_take_break()
        allowed_targets = targets.all_targets() if wants_break else llm_work_targets(self, targets)
        data = await llm_client.complete_agent_decision(system, user, allowed_targets)
        data = normalize_llm_decision(data, active_task)
        if self.assigned_meeting_seat and self._directive_is_meeting():
            data["movement_type"] = "meeting"
            data["target_id"] = self.assigned_meeting_seat
        travel_mode = self._resolve_tool_target(data, targets, wants_break, agents)
        if travel_mode == "normal":
            travel_mode = self._route_to_collaborator(data, targets, agents)
        target_id = data.get("target_id")
        if target_id not in targets.all_targets():
            work_targets = llm_work_targets(self, targets)
            target_id = targets.own_desk(self.worker_id) or (work_targets[0] if work_targets else None)
            if target_id is None:
                return None
            data["target_id"] = target_id
        if target_id in targets.idle_points and not wants_break:
            work_targets = llm_work_targets(self, targets)
            target_id = targets.own_desk(self.worker_id) or (work_targets[0] if work_targets else target_id)
            data["target_id"] = target_id
        travel_mode = enforce_fixed_workstation_target(self, data, targets, travel_mode)
        target_id = data.get("target_id")

        self.status = str(data.get("status", self.status))[:30]
        self.mood = str(data.get("mood", self.mood))[:20]
        self.focus_task = str(data.get("focus_task", self.focus_task))[:60]
        self._apply_work_context(data, company)
        self.remember(f"LLM决策:{target_id}:{self.status}")
        return self._move_command(str(target_id), str(data.get("say", ""))[:60], data, travel_mode)

    def _decide_with_rules(self, targets: OfficeTargets, company: CompanyProject) -> AgentCommand:
        behavior = choose_rule_behavior(self, targets, company)
        if behavior is None or not behavior.target_id:
            return self._idle_command("暂时没有合适目标。")

        target_id = behavior.target_id
        self.status = status_for_behavior_state(behavior.state, bool(self.current_directive or behavior.active_task))
        self.mood = self._mood_for_state()
        if behavior.state == STATE_BREAK:
            self.energy = min(1.0, self.energy + 0.08)
            self.stress = max(0.05, self.stress - 0.08)
        context = build_rule_work_context(self, target_id, behavior.active_task)
        context["behavior_state"] = behavior.state
        self._apply_work_context(context, company)
        self.remember(f"规则决策:{target_id}:{self.focus_task}")
        return self._move_command(target_id, str(context["say"]), context, behavior.travel_mode)

    def _should_take_break(self) -> bool:
        if self.energy <= settings.low_energy_rest_threshold:
            return True
        if self.stress >= settings.high_stress_rest_threshold:
            return True
        return random.random() < settings.break_chance

    def _directive_is_meeting(self) -> bool:
        meeting_words = ["讨论", "开会", "会议", "评审", "对齐", "同步", "复盘", "碰一下"]
        return any(word in self.current_directive for word in meeting_words)

    def _mood_for_state(self) -> str:
        if self.energy < 0.35:
            return "有点疲惫"
        if self.current_directive:
            return "专注"
        return random.choice(["平稳", "投入", "略微分心"])

    def _apply_work_context(self, data: dict[str, object], company: CompanyProject) -> None:
        self.needs_help_from = text_value(data.get("needs_help_from", ""))[:20]
        self.current_risk = text_value(data.get("risk_note", ""))[:100]
        self.confirmation_question = text_value(data.get("confirmation_question", ""))[:100]
        collaboration_resolved = bool(data.get("collaboration_resolved", False))
        if collaboration_resolved:
            self.needs_help_from = ""
            self.confirmation_question = ""
            if not self.current_risk:
                self.stress = max(0.05, self.stress - 0.04)

        memory_note = text_value(data.get("memory_note", "")).strip()
        work_update = text_value(data.get("work_update", "")).strip()
        if memory_note:
            self.remember(f"工作记忆:{memory_note[:120]}")
        if work_update and self.active_task_id in company.tasks:
            task = company.tasks[self.active_task_id]
            task.notes.append(f"{self.name}: {work_update[:120]}")
            task.notes = task.notes[-8:]
        if self.current_risk:
            self.stress = min(1.0, self.stress + 0.03)
            self.remember(f"风险:{self.current_risk}")
        if self.needs_help_from:
            self.remember(f"需要协作:{self.needs_help_from}")
        if self.confirmation_question:
            self.remember(f"待确认:{self.confirmation_question}")

    def _resolve_tool_target(self, data: dict[str, object], targets: OfficeTargets, wants_break: bool, agents: dict[str, "OfficeAgent"]) -> str:
        movement_type = text_value(data.get("movement_type", "")).strip()
        colleague_id = text_value(data.get("colleague_id", "")).strip()
        if not colleague_id:
            colleague_id = text_value(data.get("needs_help_from", "")).strip()
        colleague_id = normalize_colleague_id(colleague_id, self.worker_id, agents)

        if movement_type == "visit_colleague" and colleague_id and colleague_id != self.worker_id:
            helper_desk = targets.own_desk(colleague_id)
            if helper_desk:
                data["target_id"] = helper_desk
                data["needs_help_from"] = colleague_id
                self.seeking_helper_id = colleague_id
                self.checked_helper_desk = False
                return "visit"

        if movement_type == "meeting":
            if self.assigned_meeting_seat:
                data["target_id"] = self.assigned_meeting_seat
                return "normal"

        if movement_type == "break" and wants_break and targets.idle_points:
            data["target_id"] = random.choice(targets.idle_points)
            return "normal"

        own_desk = targets.own_desk(self.worker_id)
        if movement_type in ["own_desk", "stay", "break", ""] and own_desk:
            data["target_id"] = own_desk
            return "normal"

        if own_desk:
            data["target_id"] = own_desk
        return "normal"

    def _route_to_collaborator(self, data: dict[str, object], targets: OfficeTargets, agents: dict[str, "OfficeAgent"]) -> str:
        """需要和同事沟通时，先去对方工位找人，不直接抢座位。"""
        helper_id = normalize_colleague_id(text_value(data.get("needs_help_from", "")).strip(), self.worker_id, agents)
        if not helper_id or helper_id == self.worker_id:
            return "normal"
        helper_desk = targets.own_desk(helper_id)
        if helper_desk:
            data["target_id"] = helper_desk
            self.seeking_helper_id = helper_id
            self.checked_helper_desk = False
            intent = text_value(data.get("intent", ""))
            if "找" not in intent and "沟通" not in intent:
                data["intent"] = f"{intent}；先去找 {helper_id} 当面同步。".strip("；")
            return "visit"
        return "normal"

    async def _continue_find_person_flow(self, event: WorkerEvent, targets: OfficeTargets, agents: dict[str, "OfficeAgent"]) -> AgentCommand | None:
        if not self.seeking_helper_id or event.type != "worker_arrived":
            return None

        helper = agents.get(self.seeking_helper_id)
        helper_desk = targets.own_desk(self.seeking_helper_id)
        if helper is None or helper_desk is None:
            self.seeking_helper_id = ""
            self.checked_helper_desk = False
            return None

        arrived_target = event.target_id or ""
        helper_last_target = helper.last_target_id or helper_desk
        helper_is_at_desk = helper_last_target == helper_desk

        if arrived_target == helper_desk and not helper_is_at_desk and not self.checked_helper_desk:
            self.checked_helper_desk = True
            target_id = helper_last_target if helper_last_target in targets.all_targets() else helper_desk
            context = {
                "intent": f"{helper.name} 不在工位，改去他当前所在位置找人",
                "work_update": f"去 {target_id} 找 {helper.name} 当面沟通",
                "risk_note": "",
                "needs_help_from": self.seeking_helper_id,
                "confirmation_question": "",
                "memory_note": f"{helper.name} 工位没人，转去 {target_id} 找他",
                "confidence": 0.86,
                "stream_lines": [
                    f"{helper.name} 工位没人。",
                    f"我去他现在所在的位置找他。",
                ],
            }
            memory_note = text_value(context.get("memory_note", "")).strip()
            if memory_note:
                self.remember(f"工作记忆:{memory_note}")
            return self._move_command(target_id, f"{helper.name} 不在工位，我去他那边找。", context, "visit")

        if arrived_target == helper_last_target or (arrived_target == helper_desk and helper_is_at_desk):
            context = await self._collaboration_context(helper)
            self.seeking_helper_id = ""
            self.checked_helper_desk = False
            self.needs_help_from = ""
            self.status = f"和{helper.name}沟通"
            helper.status = f"回应{self.name}的协作"
            helper.pending_interaction_from = self.worker_id
            helper.pending_interaction_say = text_value(context.get("helper_say", ""))
            helper.pending_interaction_context = dict(context)
            helper.remember(f"协作回应:{self.name}:{text_value(context.get('confirmation_question', '')) or text_value(context.get('work_update', ''))}")
            self.remember(f"协作沟通:{helper.name}:{text_value(context.get('work_update', ''))}")
            return self._say_command(text_value(context.get("say", "")), context)
        return None

    async def _collaboration_context(self, helper: "OfficeAgent") -> dict[str, object]:
        question = self.confirmation_question or f"{helper.name}，我需要你帮我确认一下「{self.focus_task}」这块。"
        try:
            data = await llm_client.complete_colleague_reply(
                requester_name=self.name,
                requester_role=self.role,
                helper_name=helper.name,
                helper_role=helper.role,
                helper_prompt=helper.roleplay_prompt(),
                question=question,
                task_title=self.focus_task,
                risk_note=self.current_risk,
            )
        except Exception:
            data = {}
        if data.get("reply") and data.get("work_update"):
            reply = clean_visible_text(data.get("reply", ""))
            next_step = clean_visible_text(data.get("next_step", "")) or clean_visible_text(data.get("work_update", ""))
            return {
                "intent": f"当面找 {helper.name} 处理协作问题",
                "work_update": clean_visible_text(data.get("work_update", "")),
                "risk_note": clean_visible_text(data.get("risk_note", "")) or self.current_risk,
                "needs_help_from": "",
                "confirmation_question": question,
                "confidence": safe_confidence(data.get("confidence", 0.0)),
                "behavior_state": "collaboration_loop",
                "stream_lines": [
                    f"已找到 {helper.name}。",
                    f"{helper.name}回应：{reply}",
                    next_step,
                ],
                "say": f"{self.name}：明白，我按这个去推进。",
                "helper_say": f"{helper.name}：{reply}",
            }

        if "测试" in helper.role:
            update = f"请 {helper.name} 补验收点和回归风险"
        elif "产品" in helper.role:
            update = f"请 {helper.name} 明确用户场景和验收口径"
        elif "后端" in helper.role or "架构" in helper.role:
            update = f"请 {helper.name} 确认接口边界和技术风险"
        elif "前端" in helper.role or "UI" in helper.role:
            update = f"请 {helper.name} 确认页面状态和交互细节"
        else:
            update = f"请 {helper.name} 补充他负责范围的结论"
        return {
            "intent": f"当面找 {helper.name} 处理协作问题",
            "work_update": update,
            "risk_note": self.current_risk,
            "needs_help_from": "",
            "confirmation_question": question,
            "confidence": 0.84,
            "behavior_state": "collaboration_loop",
            "stream_lines": [
                f"已找到 {helper.name}。",
                update,
            ],
            "say": f"{self.name}：{question}",
            "helper_say": f"{helper.name}：我先看这块，{update}。",
        }

    def roleplay_prompt(self) -> str:
        """每个员工独立的角色扮演提示词，用于 LLM 决策和悬停详情展示。"""
        if self.roleplay_template:
            return self.roleplay_template
        return (
            f"你正在扮演 {self.name}，员工ID为 {self.worker_id}，岗位是 {self.role}。"
            f"你的性格：{self.personality}。"
            f"你的工作方式：{self.work_style}。"
            f"你的沟通风格：{self.communication_style}。"
            f"你重视：{self.work_values}。"
            f"你容易被这些情况触发压力或冲突：{self.conflict_triggers}。"
            f"你和同事的关系与协作线索：{self.relationship_notes}。"
            "你要像真实软件公司员工一样行动：玩家只给初始业务目标，后续需求补全、技术澄清、验收确认和协作沟通都在项目组内部完成。"
        )

    def _sync_task_focus(self, company: CompanyProject) -> None:
        active_task = company.tasks.get(self.active_task_id)
        if active_task and active_task.status == "done":
            active_task = None
        if active_task and active_task.assignee_id != self.worker_id:
            active_task = None
        if active_task is None:
            active_task = company.task_for_agent(self.worker_id)
        if active_task is None:
            candidate = company.best_unassigned_task_for_role(self.role)
            if candidate:
                candidate.assign_to(self.worker_id)
                self.active_task_id = candidate.task_id
                active_task = candidate
                self.remember(f"领取任务:{candidate.task_id}:{candidate.title}")

        if active_task:
            self.active_task_id = active_task.task_id
            self.focus_task = active_task.title
            if active_task.status == "doing":
                self.status = "推进任务"

    def _advance_work_if_possible(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject) -> None:
        if not self.active_task_id:
            return

        own_desk = targets.own_desk(self.worker_id)
        at_own_desk = event.target_id == own_desk and event.type == "worker_arrived"
        active_tick = event.type in ["autonomy_tick", "worker_ready"] and self.last_target_id == own_desk
        if not at_own_desk and not active_tick:
            return

        amount = random.uniform(0.06, 0.16) * max(0.35, self.energy) * max(0.4, 1.0 - self.stress * 0.35)
        completed = company.advance_task(self.active_task_id, self.name, amount)
        self.energy = max(0.08, self.energy - 0.035)
        self.stress = min(1.0, self.stress + 0.02)
        if completed:
            self.completed_task_count += 1
            self.remember(f"完成任务:{self.active_task_id}")
            self.status = "完成任务"
            self.mood = "有成就感"
            self.current_directive = ""
            self.active_task_id = ""

    def _react_to_current_place(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject) -> AgentCommand | None:
        """到达后的反应层：先在当前位置工作/休息，不立刻乱发新移动。"""
        if event.type not in ["worker_arrived", "worker_ready", "autonomy_tick"]:
            return None

        active_task = company.tasks.get(self.active_task_id)
        own_desk = targets.own_desk(self.worker_id)
        if active_task and own_desk and self.last_target_id == own_desk:
            self.status = "在工位推进任务"
            self.focus_task = active_task.title
            context = {
                "intent": "留在本人固定工位推进当前任务",
                "work_update": f"继续处理「{active_task.title}」",
                "risk_note": self.current_risk,
                "needs_help_from": self.needs_help_from,
                "confirmation_question": self.confirmation_question,
                "confidence": 0.82,
                "behavior_state": "work_loop",
                "stream_lines": [
                    f"在工位推进：{active_task.title}",
                    f"当前进度约 {active_task.progress:.0%}",
                ],
            }
            return self._idle_command("", context)

        if self.last_target_id in targets.idle_points and (self.energy < 0.95 or self.stress > 0.12):
            self.energy = min(1.0, self.energy + 0.06)
            self.stress = max(0.05, self.stress - 0.05)
            self.status = "短暂休息"
            context = {
                "intent": "休息恢复状态",
                "work_update": "暂停移动，恢复精力",
                "risk_note": "",
                "needs_help_from": "",
                "confirmation_question": "",
                "confidence": 0.7,
                "behavior_state": "break_loop",
                "stream_lines": ["先缓一下，恢复状态。"],
            }
            return self._idle_command("", context)
        return None

    def _move_command(self, target_id: str, say: str, context: dict[str, object] | None = None, travel_mode: str = "normal") -> AgentCommand:
        payload = self.snapshot().model_dump()
        payload["travel_mode"] = travel_mode
        if context:
            payload["behavior_state"] = text_value(context.get("behavior_state", ""))
            if text_value(context.get("behavior_state", "")).endswith("_loop"):
                payload["decision_source"] = "reaction_loop"
            payload["work_context"] = {
                "intent": str(context.get("intent", "")),
                "work_update": text_value(context.get("work_update", "")),
                "risk_note": text_value(context.get("risk_note", "")),
                "needs_help_from": text_value(context.get("needs_help_from", "")),
                "confirmation_question": text_value(context.get("confirmation_question", "")),
                "confidence": safe_confidence(context.get("confidence", 0.0)),
            }
            payload["agent_stream"] = agent_stream_lines(context, say)
        visible_say = "" if travel_mode == "meeting" or "Chair" in target_id else say
        return AgentCommand(
            worker_id=self.worker_id,
            action="move_to",
            target_id=target_id,
            say=visible_say,
            payload=payload,
        )

    def _idle_command(self, say: str, context: dict[str, object] | None = None) -> AgentCommand:
        payload = self.snapshot().model_dump()
        if context:
            payload["behavior_state"] = text_value(context.get("behavior_state", ""))
            if text_value(context.get("behavior_state", "")).endswith("_loop"):
                payload["decision_source"] = "reaction_loop"
            payload["work_context"] = {
                "intent": str(context.get("intent", "")),
                "work_update": text_value(context.get("work_update", "")),
                "risk_note": text_value(context.get("risk_note", "")),
                "needs_help_from": text_value(context.get("needs_help_from", "")),
                "confirmation_question": text_value(context.get("confirmation_question", "")),
                "confidence": safe_confidence(context.get("confidence", 0.0)),
            }
            payload["agent_stream"] = agent_stream_lines(context, say)
        return AgentCommand(
            worker_id=self.worker_id,
            action="idle",
            say=say,
            payload=payload,
        )

    def _consume_pending_interaction(self) -> AgentCommand | None:
        if not self.pending_interaction_say:
            return None
        say = self.pending_interaction_say
        context = dict(self.pending_interaction_context)
        context["behavior_state"] = "collaboration_reply_loop"
        requester_id = self.pending_interaction_from
        self.pending_interaction_from = ""
        self.pending_interaction_say = ""
        self.pending_interaction_context = {}
        self.status = "回应同事协作"
        self.remember(f"协作发言:{requester_id}:{say}")
        return self._say_command(say, context)

    def _say_command(self, say: str, context: dict[str, object] | None = None) -> AgentCommand:
        payload = self.snapshot().model_dump()
        payload["display"] = "speech"
        if context:
            payload["behavior_state"] = text_value(context.get("behavior_state", ""))
            payload["decision_source"] = "reaction_loop"
            payload["work_context"] = {
                "intent": str(context.get("intent", "")),
                "work_update": text_value(context.get("work_update", "")),
                "risk_note": text_value(context.get("risk_note", "")),
                "needs_help_from": text_value(context.get("needs_help_from", "")),
                "confirmation_question": text_value(context.get("confirmation_question", "")),
                "confidence": safe_confidence(context.get("confidence", 0.0)),
            }
            payload["agent_stream"] = agent_stream_lines(context, say)
        return AgentCommand(
            worker_id=self.worker_id,
            action="say",
            say=say,
            payload=payload,
        )

    def snapshot(self) -> AgentSnapshot:
        return AgentSnapshot(
            worker_id=self.worker_id,
            name=self.name,
            role=self.role,
            personality=self.personality,
            roleplay_prompt=self.roleplay_prompt(),
            communication_style=self.communication_style,
            work_values=self.work_values,
            conflict_triggers=self.conflict_triggers,
            relationship_notes=self.relationship_notes,
            status=self.status,
            mood=self.mood,
            energy=round(self.energy, 3),
            focus_task=self.focus_task,
            current_directive=self.current_directive,
            autonomy_steps=self.autonomy_steps,
            active_task_id=self.active_task_id,
            completed_task_count=self.completed_task_count,
            stress=round(self.stress, 3),
            needs_help_from=self.needs_help_from,
            current_risk=self.current_risk,
            confirmation_question=self.confirmation_question,
            memory=memory_store.display_memory(self.worker_id, 8),
        )





