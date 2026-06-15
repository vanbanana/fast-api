import logging

from app.domain import BossDirective, CompanyProject, ProjectTask
from app.worker_agent import OfficeAgent

logger = logging.getLogger(__name__)


class ProjectPlanningService:
    """项目规划服务：用 LLM function calling 对老板目标做任务拆解。"""

    def __init__(self, company: CompanyProject, agents: dict[str, OfficeAgent]) -> None:
        self.company = company
        self.agents = agents

    async def create_directive_tasks(self, directive: BossDirective) -> dict[str, ProjectTask]:
        plan_items = await self._build_project_plan_items(directive)
        assigned: dict[str, ProjectTask] = {}
        for item in plan_items:
            worker_id = str(item.get("worker_id", ""))
            if worker_id and worker_id not in self.agents:
                continue
            # 如果 LLM 没返回 worker_id，按顺序匹配
            if not worker_id:
                worker_ids = directive.target_worker_ids or self.select_planning_workers(directive)
                for wid in worker_ids:
                    if wid not in assigned and wid in self.agents:
                        worker_id = wid
                        break
            if not worker_id or worker_id in assigned:
                continue

            task_title = str(item.get("task_title", "")).strip() or directive.text
            task_type = str(item.get("task_type", "general")).strip() or "general"
            task = self.company.create_task(task_title, task_type, directive.priority, "llm_planning", worker_id)
            contribution = str(item.get("contribution", "")).strip()
            risk_note = str(item.get("risk_note", "")).strip()
            if contribution:
                task.notes.append(contribution[:120])
            if risk_note:
                task.notes.append(f"风险:{risk_note[:100]}")
            assigned[worker_id] = task
            logger.info("[PLANNING] %s → %s [%s]", worker_id, task_title[:40], task_type)

        if not assigned:
            logger.warning("[PLANNING] 任务拆解未产生任何有效任务")
        return assigned

    async def _build_project_plan_items(self, directive: BossDirective) -> list[dict[str, str]]:
        worker_ids = directive.target_worker_ids or self.select_planning_workers(directive)
        participants = [
            {
                "worker_id": worker_id,
                "name": self.agents[worker_id].name,
                "role": self.agents[worker_id].role,
            }
            for worker_id in worker_ids
            if worker_id in self.agents
        ]

        try:
            from app.llm_client import llm_client
            plan_items = await llm_client.generate_task_planning(
                objective=directive.text,
                participants=participants,
            )
            return plan_items
        except Exception as e:
            logger.error("[PLANNING] 任务拆解失败: %s", e)
            return []

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
