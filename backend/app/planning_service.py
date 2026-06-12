from app.domain import BossDirective, CompanyProject, ProjectTask
from app.team_autogen import TeamParticipant, run_project_planning
from app.worker_agent import OfficeAgent


class ProjectPlanningService:
    """项目规划服务：用 AutoGen 团队对老板目标做任务拆解。"""

    def __init__(self, company: CompanyProject, agents: dict[str, OfficeAgent]) -> None:
        self.company = company
        self.agents = agents

    async def create_directive_tasks(self, directive: BossDirective) -> dict[str, ProjectTask]:
        plan_items = await self._build_project_plan_items(directive)
        assigned: dict[str, ProjectTask] = {}
        for item in plan_items:
            worker_id = str(item.get("worker_id", ""))
            if worker_id not in self.agents:
                continue
            task_title = str(item.get("task_title", "")).strip() or directive.text
            task_type = str(item.get("task_type", "general")).strip() or "general"
            task = self.company.create_task(task_title, task_type, directive.priority, "autogen_team", worker_id)
            contribution = str(item.get("contribution", "")).strip()
            risk_note = str(item.get("risk_note", "")).strip()
            if contribution:
                task.notes.append(contribution[:120])
            if risk_note:
                task.notes.append(f"风险:{risk_note[:100]}")
            assigned[worker_id] = task
        return assigned

    async def _build_project_plan_items(self, directive: BossDirective) -> list[dict[str, str]]:
        worker_ids = directive.target_worker_ids or self.select_planning_workers(directive)
        participants = [
            TeamParticipant(
                worker_id=worker_id,
                name=self.agents[worker_id].name,
                role=self.agents[worker_id].role,
                prompt=self.agents[worker_id].roleplay_prompt(),
            )
            for worker_id in worker_ids
            if worker_id in self.agents
        ]
        try:
            plan_items = await run_project_planning(
                objective=directive.text,
                participants=participants,
                max_turns=min(8, max(4, len(participants))),
            )
        except Exception:
            plan_items = []
        if plan_items:
            return plan_items
        return self._fallback_project_plan_items(directive, worker_ids)

    def select_planning_workers(self, directive: BossDirective) -> list[str]:
        task_type = self.company._infer_task_type(directive.text)
        if task_type == "backend":
            return ["worker1", "worker3", "worker2", "worker4", "worker9", "worker6"]
        if task_type == "design":
            return ["worker1", "worker3", "worker5", "worker9", "worker6"]
        if task_type == "qa":
            return ["worker1", "worker3", "worker6", "worker2", "worker9"]
        if task_type == "data":
            return ["worker1", "worker3", "worker8", "worker2", "worker6"]
        if task_type == "ops":
            return ["worker1", "worker7", "worker6", "worker2", "worker9"]
        return ["worker1", "worker3", "worker2", "worker9", "worker5", "worker6", "worker4", "worker8"]

    def _fallback_project_plan_items(self, directive: BossDirective, worker_ids: list[str]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for worker_id in worker_ids:
            agent = self.agents.get(worker_id)
            if agent is None:
                continue
            task_type = self.company._infer_task_type(agent.role + directive.text)
            title = directive.text
            if "项目经理" in agent.role:
                title = f"拆解「{directive.text}」范围、负责人和排期"
                task_type = "ops"
            elif "产品" in agent.role:
                title = f"补全「{directive.text}」用户场景和验收标准"
                task_type = "product"
            elif "架构" in agent.role:
                title = f"评估「{directive.text}」系统边界、模块拆分和技术风险"
                task_type = "backend"
            elif "后端" in agent.role:
                title = f"评估「{directive.text}」接口、数据结构和服务边界"
                task_type = "backend"
            elif "前端" in agent.role:
                title = f"实现「{directive.text}」页面状态和交互流程"
                task_type = "frontend"
            elif "UI" in agent.role or "设计" in agent.role:
                title = f"输出「{directive.text}」关键页面和异常状态设计"
                task_type = "design"
            elif "测试" in agent.role:
                title = f"制定「{directive.text}」验收用例和回归范围"
                task_type = "qa"
            elif "数据" in agent.role:
                title = f"定义「{directive.text}」指标和埋点口径"
                task_type = "data"
            items.append({
                "worker_id": worker_id,
                "task_title": title,
                "task_type": task_type,
                "contribution": "本地兜底规划任务",
                "risk_note": "",
            })
        return items
