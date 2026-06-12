from app.domain import BossDirective, CompanyProject, OfficeTargets
from app.meeting_session import MeetingSession
from app.directive_router import route_directive
from app.meeting_runtime import MeetingRuntime
from app.office_clock import office_clock
from app.planning_service import ProjectPlanningService
from app.schemas import AgentCommand, AgentSnapshot, BossCommand, CompanySnapshot, ProjectTaskSnapshot, WorkerEvent
from app.worker_agent import OfficeAgent
from app.worker_profile_loader import load_agent_profiles, make_agent

class OfficeRuntime:
    """轻量多 agent runtime，先服务当前 Godot demo，后续可平滑升级为任务图框架。"""

    def __init__(self) -> None:
        self.targets = OfficeTargets()
        self.company = CompanyProject()
        self.directives: list[BossDirective] = []
        self.agents: dict[str, OfficeAgent] = load_agent_profiles()
        self.planning = ProjectPlanningService(self.company, self.agents)
        self.meeting = MeetingRuntime(self.company, self.targets, self.agents, self.planning)

    @property
    def active_meeting(self) -> MeetingSession | None:
        """兼容旧调试和测试入口；真实会议状态在 MeetingRuntime 内。"""
        return self.meeting.active_session

    async def handle_event(self, event: WorkerEvent) -> list[AgentCommand]:
        if event.type == "world_snapshot":
            self._update_targets(event)
            self._reset_runtime_for_new_scene()
            return (
                [AgentCommand(worker_id=event.worker_id, action="idle", say="后端已同步场景目标。")]
                + self.profile_commands()
            )

        if event.type == "boss_command":
            command = BossCommand.model_validate(event.payload)
            return await self.apply_boss_command(command)

        if self.meeting.is_meeting_event(event):
            meeting_commands = await self.meeting.handle_event(event)
            if meeting_commands or self.meeting.consumes_event(event):
                return meeting_commands
        if self.meeting.locks_worker(event.worker_id):
            return [self.meeting.locked_worker_command(event)]

        if event.type == "autonomy_tick":
            office_clock.advance(1.0 / max(1, len(self.agents)))
        agent = self._get_or_create_agent(event.worker_id)
        return self._with_stream_commands([await agent.decide(event, self.targets, self.company, self.agents)])

    async def apply_boss_command(self, command: BossCommand) -> list[AgentCommand]:
        directive = BossDirective(
            text=command.text,
            priority=command.priority,
            target_worker_ids=command.target_worker_ids,
        )
        self.directives.append(directive)
        self.directives = self.directives[-20:]
        route = await route_directive(directive)
        if route.is_meeting:
            commands = self.meeting.start_meeting(directive)
            for command_item in commands:
                command_item.payload["directive_route"] = route.__dict__
            return commands
        assigned_tasks = await self.planning.create_directive_tasks(directive)

        commands: list[AgentCommand] = []
        for agent in self.agents.values():
            if agent.worker_id not in assigned_tasks:
                continue
            agent.apply_directive(directive, assigned_tasks.get(agent.worker_id))
            command_item = await agent.decide(WorkerEvent(type="boss_command", worker_id=agent.worker_id), self.targets, self.company, self.agents)
            command_item.payload["decision_source"] = "rule_immediate"
            command_item.payload["directive_route"] = route.__dict__
            commands.append(command_item)
        return self._with_stream_commands(commands)

    async def autonomy_tick(self, worker_ids: list[str] | None = None) -> list[AgentCommand]:
        """主动推进员工自主循环，给网页模拟或定时器使用。"""
        office_clock.advance()
        commands: list[AgentCommand] = []
        selected_ids = set(worker_ids or [])
        for agent in self.agents.values():
            if selected_ids and agent.worker_id not in selected_ids:
                continue
            if self.meeting.locks_worker(agent.worker_id):
                continue
            event = WorkerEvent(type="autonomy_tick", worker_id=agent.worker_id)
            commands.append(await agent.decide(event, self.targets, self.company, self.agents))
        return self._with_stream_commands(commands)

    def _with_stream_commands(self, commands: list[AgentCommand]) -> list[AgentCommand]:
        expanded: list[AgentCommand] = []
        for command in commands:
            stream_lines = command.payload.get("agent_stream", [])
            is_silent_roam = command.payload.get("travel_mode") == "roam" and not command.say
            is_rule_immediate = command.payload.get("decision_source") == "rule_immediate"
            is_reaction_loop = command.payload.get("decision_source") == "reaction_loop"
            if isinstance(stream_lines, list) and not is_silent_roam and not is_rule_immediate and not is_reaction_loop:
                for index, line in enumerate(stream_lines):
                    text = str(line).strip()
                    if not text:
                        continue
                    expanded.append(AgentCommand(
                        worker_id=command.worker_id,
                        action="stream_delta",
                        say=text,
                        payload={
                            "line": text,
                            "index": index,
                            "source_action": command.action,
                        },
                    ))
            expanded.append(command)
        return expanded

    def snapshots(self) -> list[AgentSnapshot]:
        return [agent.snapshot() for agent in self.agents.values()]

    def profile_commands(self) -> list[AgentCommand]:
        """连接后先把真实员工画像推给 Godot，避免界面只显示 worker1/worker2。"""
        commands: list[AgentCommand] = []
        for agent in self.agents.values():
            payload = agent.snapshot().model_dump()
            payload["decision_source"] = "profile_update"
            commands.append(
                AgentCommand(
                    worker_id=agent.worker_id,
                    action="idle",
                    say="",
                    payload=payload,
                )
            )
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
        for agent in self.agents.values():
            agent.reset_runtime_state()

    def _get_or_create_agent(self, worker_id: str) -> OfficeAgent:
        agent = self.agents.get(worker_id)
        if agent is None:
            agent = make_agent(
                worker_id,
                worker_id,
                "临时员工",
                "普通，正在适应团队节奏",
                "根据现场情况行动",
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


office_runtime = OfficeRuntime()





