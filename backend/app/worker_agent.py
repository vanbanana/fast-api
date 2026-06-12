"""单个员工 agent。

职责收敛为编排：事件记录 -> 协作流 -> 任务同步 -> 反应层 -> 决策。
决策由 LLM 或规则行为树产出统一的 WorkerDecision（意图），再经
worker_intent.resolve_decision 解析为具体目标；运行态由 fsm 承载。
"""
import random
from dataclasses import dataclass, field

from app.config import settings
from app.domain import BossDirective, CompanyProject, OfficeTargets, ProjectTask
from app.llm_client import llm_client
from app.memory import memory_store
from app.schemas import AgentCommand, AgentSnapshot, WorkerEvent
from app.worker_behavior_tree import STATE_BREAK, STATE_FREE_ROAM, STATE_MEETING_ASSIGNED, STATE_WAIT, choose_rule_behavior, should_take_break, status_for_behavior_state
from app.worker_collaboration import consume_pending_reply, continue_find_person_flow
from app.worker_decision_policy import normalize_colleague_id
from app.worker_intent import ActionIntent, WorkerDecision, apply_resolution, downgrade_to_desk, resolve_decision
from app.worker_llm_decision import agent_stream_lines, build_agent_decision_messages, decision_from_llm_data, safe_confidence, text_value
from app.worker_rule_context import build_confirmation_question, build_rule_work_context, should_confirm_with_team
from app.worker_state import WorkerState, WorkerStateMachine

_STATE_BY_INTENT = {
    ActionIntent.WORK_AT_DESK: WorkerState.WORKING,
    ActionIntent.VISIT_COLLEAGUE: WorkerState.SEEKING_COLLEAGUE,
    ActionIntent.JOIN_MEETING: WorkerState.MEETING,
    ActionIntent.TAKE_BREAK: WorkerState.RESTING,
    ActionIntent.ROAM: WorkerState.ROAMING,
    ActionIntent.STAY: WorkerState.IDLE,
}

_INTENT_BY_BEHAVIOR_STATE = {
    STATE_MEETING_ASSIGNED: ActionIntent.JOIN_MEETING,
    STATE_BREAK: ActionIntent.TAKE_BREAK,
    STATE_FREE_ROAM: ActionIntent.ROAM,
    STATE_WAIT: ActionIntent.STAY,
}


@dataclass
class OfficeAgent:
    """单个员工，拥有独立人设、职业职责、短期记忆和显式运行态状态机。"""

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
    assigned_meeting_seat: str = ""
    dwell_ticks: int = 0
    errand_helper_id: str = ""
    interrupted_task_id: str = ""
    interrupted_focus_task: str = ""
    fsm: WorkerStateMachine = field(default_factory=WorkerStateMachine)
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
        self.assigned_meeting_seat = ""
        self.dwell_ticks = 0
        self.errand_helper_id = ""
        self.interrupted_task_id = ""
        self.interrupted_focus_task = ""
        self.fsm.reset()

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

    def interrupt_for_errand(self, directive: BossDirective, helper_id: str) -> None:
        """老板指派优先级高于当前工作：暂存手头任务，立刻去找指定同事，办完自动恢复。"""
        if self.active_task_id:
            self.interrupted_task_id = self.active_task_id
            self.interrupted_focus_task = self.focus_task
        self.errand_helper_id = helper_id
        self.current_directive = directive.text
        self.focus_task = directive.text
        self.confirmation_question = directive.text[:100]
        self.active_task_id = ""
        self.status = "执行老板指派"
        self.mood = "被点名"
        self.autonomy_steps = 0
        self.cooldown_steps = 0
        self.dwell_ticks = 0
        self.fsm.force(WorkerState.IDLE)
        self.fsm.start_seeking(helper_id)
        self.needs_help_from = helper_id
        self.remember(f"老板指派:{directive.text}")

    def finish_errand(self) -> None:
        """指派办完：恢复被打断的任务，不影响原有工作节奏。"""
        if not self.errand_helper_id:
            return
        self.errand_helper_id = ""
        self.current_directive = ""
        self.confirmation_question = ""
        if self.interrupted_task_id:
            self.active_task_id = self.interrupted_task_id
            self.focus_task = self.interrupted_focus_task or self.focus_task
            self.status = "回到原任务"
            self.remember(f"办完老板指派，回到原任务:{self.focus_task}")
        self.interrupted_task_id = ""
        self.interrupted_focus_task = ""

    async def decide(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject, agents: dict[str, "OfficeAgent"] | None = None) -> AgentCommand:
        agents = agents or {}
        self._record_event(event)
        pending_command = consume_pending_reply(self)
        if pending_command:
            return pending_command
        find_command = await continue_find_person_flow(self, event, targets, agents)
        if find_command:
            return find_command
        chat_command = self._maybe_water_cooler_chat(event, targets, agents)
        if chat_command:
            return chat_command
        self._sync_task_focus(company)
        submit_command = self._advance_work_if_possible(event, targets, company)
        if submit_command:
            return submit_command
        review_command = self._review_tasks_if_qa(event, targets, company, agents)
        if review_command:
            return review_command
        reaction_command = self._react_to_current_place(event, targets, company)
        if reaction_command:
            return reaction_command
        if event.type == "autonomy_tick" and self.dwell_ticks > 0:
            self.dwell_ticks -= 1
            return self.idle_command("")
        if not targets.all_targets():
            return self.idle_command("还没有同步场景目标。")

        if self.autonomy_steps >= settings.max_autonomy_steps:
            self.cooldown_steps = settings.loop_cooldown_steps
            self.autonomy_steps = 0
            self.fsm.transition(WorkerState.COOLDOWN)
            return self.idle_command("自主循环达到上限，短暂整理状态。")

        if self.cooldown_steps > 0:
            self.cooldown_steps -= 1
            return self.idle_command("整理记忆和任务优先级。")

        self.autonomy_steps += 1
        decision: WorkerDecision | None = None
        if self._should_use_llm(event, company):
            decision = await self._llm_decision(event, company, agents)
        if decision is None:
            decision = self._rule_decision(targets, company)
        return self._execute_decision(decision, targets, company, agents)

    def _should_use_llm(self, event: WorkerEvent, company: CompanyProject) -> bool:
        """boss 指令即时反馈走规则；只有有明确工作上下文时才花 LLM 配额。"""
        if event.type == "boss_command":
            return False
        if not self.current_directive and not company.task_for_agent(self.worker_id):
            return False
        return random.random() <= settings.llm_decision_chance

    async def _llm_decision(self, event: WorkerEvent, company: CompanyProject, agents: dict[str, "OfficeAgent"]) -> WorkerDecision | None:
        active_task = company.tasks.get(self.active_task_id)
        colleagues = [f"{agent.worker_id}={agent.name}/{agent.role}" for agent in agents.values() if agent.worker_id != self.worker_id]
        system, user = build_agent_decision_messages(self, event, company, active_task, colleagues)
        try:
            data = await llm_client.complete_agent_decision(system, user)
        except Exception:
            return None
        if not data:
            return None
        decision = decision_from_llm_data(data, active_task)
        decision.helper_id = normalize_colleague_id(decision.helper_id or decision.needs_help_from, self.worker_id, agents)
        if decision.helper_id:
            decision.needs_help_from = decision.helper_id
        if decision.helper_id and decision.intent in (ActionIntent.WORK_AT_DESK, ActionIntent.STAY):
            decision.intent = ActionIntent.VISIT_COLLEAGUE
        if self.assigned_meeting_seat and self._directive_is_meeting():
            decision.intent = ActionIntent.JOIN_MEETING
        return decision

    def _rule_decision(self, targets: OfficeTargets, company: CompanyProject) -> WorkerDecision:
        behavior = choose_rule_behavior(self, targets, company)
        if behavior is None:
            return WorkerDecision(intent=ActionIntent.STAY, intent_text="暂时没有合适目标", source="rule")
        intent = _INTENT_BY_BEHAVIOR_STATE.get(behavior.state, ActionIntent.WORK_AT_DESK)
        decision = WorkerDecision(intent=intent, behavior_state=behavior.state, source="rule")
        decision.status = status_for_behavior_state(behavior.state, bool(self.current_directive or behavior.active_task))
        return decision

    def _execute_decision(self, decision: WorkerDecision, targets: OfficeTargets, company: CompanyProject, agents: dict[str, "OfficeAgent"]) -> AgentCommand:
        wants_break = should_take_break(self)
        if self.fsm.can_transition(_STATE_BY_INTENT[decision.intent]):
            resolved = resolve_decision(
                self, decision, targets, set(agents.keys()),
                allow_break=wants_break or decision.source == "rule",
            )
        else:
            resolved = downgrade_to_desk(self, targets, "当前状态不允许这个动作，先回工位推进")
        decision = apply_resolution(decision, resolved)

        if decision.source == "rule":
            active_task = company.tasks.get(self.active_task_id)
            context = build_rule_work_context(self, decision.target_id or "本工位", active_task)
            decision.intent_text = decision.intent_text or str(context["intent"])
            decision.say = decision.say or str(context["say"])
            decision.work_update = str(context["work_update"])
            decision.risk_note = str(context.get("risk_note", ""))
            decision.needs_help_from = str(context.get("needs_help_from", ""))
            decision.confirmation_question = str(context.get("confirmation_question", ""))
            decision.confidence = float(context.get("confidence", 0.68))
            decision.memory_note = str(context.get("memory_note", ""))
            self.mood = self._mood_for_state()

        self._apply_fsm_for_intent(decision)
        if decision.status:
            self.status = decision.status[:30]
        if decision.mood:
            self.mood = decision.mood[:20]
        if decision.focus_task:
            self.focus_task = decision.focus_task[:60]
        if decision.intent == ActionIntent.TAKE_BREAK:
            self.energy = min(1.0, self.energy + 0.08)
            self.stress = max(0.05, self.stress - 0.08)
        self._apply_work_context(decision.as_context(), company)
        self.remember(f"{'LLM' if decision.source == 'llm' else '规则'}决策:{decision.target_id}:{self.status}")

        if decision.intent == ActionIntent.STAY or not decision.target_id:
            return self.idle_command(decision.say, decision.as_context())
        return self.move_command(decision.target_id, decision.say, decision.as_context(), decision.travel_mode)

    def _apply_fsm_for_intent(self, decision: WorkerDecision) -> None:
        target_state = _STATE_BY_INTENT[decision.intent]
        if decision.intent == ActionIntent.VISIT_COLLEAGUE:
            if not self.fsm.start_seeking(decision.helper_id):
                return
            self.needs_help_from = decision.helper_id
            return
        self.fsm.transition(target_state)

    def _record_event(self, event: WorkerEvent) -> None:
        self.last_target_id = event.target_id or self.last_target_id
        if event.type == "worker_arrived":
            self.energy = max(0.1, self.energy - 0.015)
            self.status = f"到达 {event.target_id}"
            self.dwell_ticks = 2

    def _maybe_water_cooler_chat(self, event: WorkerEvent, targets: OfficeTargets, agents: dict[str, "OfficeAgent"]) -> AgentCommand | None:
        """休息区/走动点碰到同事时低概率寒暄，增加办公室烟火气。"""
        if event.type != "worker_arrived":
            return None
        spot = event.target_id or ""
        if spot not in targets.idle_points and spot not in targets.roam_points:
            return None
        nearby = [a for a in agents.values() if a.worker_id != self.worker_id and a.last_target_id == spot]
        if not nearby or random.random() > 0.4:
            return None
        colleague = random.choice(nearby)
        line = random.choice([
            f"{colleague.name}也在啊，今天那块顺利不？",
            f"正好碰到{colleague.name}，缓会儿再回去。",
            f"和{colleague.name}随便聊了两句。",
        ])
        self.remember(f"茶水间闲聊:{colleague.name}")
        context = {
            "intent": f"在休息区碰到 {colleague.name}，随口聊两句",
            "work_update": "",
            "risk_note": "",
            "needs_help_from": "",
            "confirmation_question": "",
            "confidence": 0.5,
            "behavior_state": "social_loop",
            "stream_lines": [f"碰到 {colleague.name} 了，闲聊两句。"],
        }
        return self.say_command(line, context)

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
        if self.errand_helper_id:
            return
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

    def _advance_work_if_possible(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject) -> AgentCommand | None:
        if not self.active_task_id:
            return None

        own_desk = targets.own_desk(self.worker_id)
        at_own_desk = event.target_id == own_desk and event.type == "worker_arrived"
        active_tick = event.type in ["autonomy_tick", "worker_ready"] and self.last_target_id == own_desk
        if not at_own_desk and not active_tick:
            return None

        task = company.tasks.get(self.active_task_id)
        amount = random.uniform(0.06, 0.16) * max(0.35, self.energy) * max(0.4, 1.0 - self.stress * 0.35)
        completed = company.advance_task(self.active_task_id, self.name, amount)
        self.energy = max(0.08, self.energy - 0.035)
        self.stress = min(1.0, self.stress + 0.02)
        if task and task.status == "review":
            submitted_task_id = self.active_task_id
            self.remember(f"提测任务:{submitted_task_id}:{task.title}")
            self.status = "已提测，等验收"
            self.current_directive = ""
            self.active_task_id = ""
            self.fsm.transition(WorkerState.IDLE)
            say = random.choice([
                f"「{task.title[:18]}」这块我提测了，等验收。",
                "这块我做完了，丢给测试看看。",
                "提测了，有问题随时叫我。",
            ])
            context = {
                "intent": f"完成开发，提测「{task.title[:30]}」",
                "work_update": f"任务 {submitted_task_id} 进入验收阶段",
                "risk_note": "",
                "needs_help_from": "",
                "confirmation_question": "",
                "confidence": 0.85,
                "behavior_state": "submit_review_loop",
                "stream_lines": [f"提测：{task.title}", "等测试验收。"],
            }
            return self.say_command(say, context)
        if completed:
            self.completed_task_count += 1
            self.remember(f"完成任务:{self.active_task_id}")
            self.status = "完成任务"
            self.mood = "有成就感"
            self.current_directive = ""
            self.active_task_id = ""
            self.fsm.transition(WorkerState.IDLE)
        return None

    def _review_tasks_if_qa(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject, agents: dict[str, "OfficeAgent"]) -> AgentCommand | None:
        """测试验收循环：提测任务由测试在工位验收，通过则完成，不通过打回给原开发。"""
        if "测试" not in self.role:
            return None
        if event.type not in ["autonomy_tick", "worker_ready", "worker_arrived"]:
            return None
        own_desk = targets.own_desk(self.worker_id)
        if not own_desk or self.last_target_id != own_desk:
            return None
        task = company.next_review_task()
        if task is None:
            return None
        dev = agents.get(task.assignee_id or "")
        dev_name = dev.name if dev else "开发"
        pass_chance = 0.6 if task.rework_count == 0 else 0.85
        if random.random() < pass_chance:
            task.pass_review(f"{self.name} 验收通过")
            say = random.choice([
                f"「{task.title[:18]}」我过了一遍，没什么问题，算完成了。",
                f"{dev_name}那块验收通过了，没问题。",
                "这轮验收跑完了，可以合入。",
            ])
            if dev:
                dev.completed_task_count += 1
                dev.mood = "有成就感"
                dev.stress = max(0.05, dev.stress - 0.05)
                dev.remember(f"验收通过:{task.task_id}:{task.title}")
            result_line = "验收通过，任务完成。"
        else:
            task.fail_review(f"{self.name} 验收打回")
            say = random.choice([
                f"{dev_name}，「{task.title[:18]}」有个边界没处理，打回你再看看。",
                f"{dev_name}，这块有个场景跑不过，麻烦再修一下。",
                f"不行，异常分支有问题，还得回给{dev_name}改。",
            ])
            if dev:
                dev.stress = min(1.0, dev.stress + 0.06)
                dev.mood = "有点烦"
                dev.remember(f"被打回:{task.task_id}:{task.title}")
            result_line = f"打回给 {dev_name} 返工。"
        self.status = "验收任务"
        self.remember(f"验收:{task.task_id}:{result_line}")
        context = {
            "intent": f"验收提测任务「{task.title[:30]}」",
            "work_update": result_line,
            "risk_note": "",
            "needs_help_from": "",
            "confirmation_question": "",
            "confidence": 0.8,
            "behavior_state": "qa_review_loop",
            "stream_lines": [f"验收：{task.title}", result_line],
        }
        return self.say_command(say, context)

    def _react_to_current_place(self, event: WorkerEvent, targets: OfficeTargets, company: CompanyProject) -> AgentCommand | None:
        """到达后的反应层：先在当前位置工作/休息，不立刻乱发新移动。"""
        if event.type not in ["worker_arrived", "worker_ready", "autonomy_tick"]:
            return None

        active_task = company.tasks.get(self.active_task_id)
        own_desk = targets.own_desk(self.worker_id)
        if active_task and own_desk and self.last_target_id == own_desk:
            self.fsm.transition(WorkerState.WORKING)
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
            return self.idle_command("", context)

        if self.last_target_id in targets.idle_points and (self.energy < 0.95 or self.stress > 0.12):
            self.fsm.transition(WorkerState.RESTING)
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
            return self.idle_command("", context)
        return None

    def move_command(self, target_id: str, say: str, context: dict[str, object] | None = None, travel_mode: str = "normal") -> AgentCommand:
        payload = self.snapshot().model_dump()
        payload["travel_mode"] = travel_mode
        if context:
            payload["behavior_state"] = text_value(context.get("behavior_state", ""))
            if text_value(context.get("behavior_state", "")).endswith("_loop"):
                payload["decision_source"] = "reaction_loop"
            payload["work_context"] = self._work_context_payload(context)
            payload["agent_stream"] = agent_stream_lines(context, say)
        visible_say = "" if travel_mode == "meeting" or "Chair" in target_id else say
        return AgentCommand(
            worker_id=self.worker_id,
            action="move_to",
            target_id=target_id,
            say=visible_say,
            payload=payload,
        )

    def idle_command(self, say: str, context: dict[str, object] | None = None) -> AgentCommand:
        payload = self.snapshot().model_dump()
        if context:
            payload["behavior_state"] = text_value(context.get("behavior_state", ""))
            if text_value(context.get("behavior_state", "")).endswith("_loop"):
                payload["decision_source"] = "reaction_loop"
            payload["work_context"] = self._work_context_payload(context)
            payload["agent_stream"] = agent_stream_lines(context, say)
        return AgentCommand(
            worker_id=self.worker_id,
            action="idle",
            say=say,
            payload=payload,
        )

    def say_command(self, say: str, context: dict[str, object] | None = None) -> AgentCommand:
        payload = self.snapshot().model_dump()
        payload["display"] = "speech"
        if context:
            payload["behavior_state"] = text_value(context.get("behavior_state", ""))
            payload["decision_source"] = "reaction_loop"
            payload["work_context"] = self._work_context_payload(context)
            payload["agent_stream"] = agent_stream_lines(context, say)
        return AgentCommand(
            worker_id=self.worker_id,
            action="say",
            say=say,
            payload=payload,
        )

    def _work_context_payload(self, context: dict[str, object]) -> dict[str, object]:
        return {
            "intent": str(context.get("intent", "")),
            "work_update": text_value(context.get("work_update", "")),
            "risk_note": text_value(context.get("risk_note", "")),
            "needs_help_from": text_value(context.get("needs_help_from", "")),
            "confirmation_question": text_value(context.get("confirmation_question", "")),
            "confidence": safe_confidence(context.get("confidence", 0.0)),
        }

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
