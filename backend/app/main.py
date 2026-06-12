from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect

from app.runtime import office_runtime
from app.schemas import AgentCommand, AgentSnapshot, BossCommand, CompanySnapshot, ProjectTaskSnapshot, WorkerEvent

app = FastAPI(title="Purr-formance Office Agents")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agents")
async def agents() -> list[AgentSnapshot]:
    """查看所有员工当前状态，给网页调试面板使用。"""
    return office_runtime.snapshots()


@app.get("/company")
async def company() -> CompanySnapshot:
    """查看公司项目、风险、士气、员工和任务总览。"""
    return office_runtime.company_snapshot()


@app.get("/tasks")
async def tasks() -> list[ProjectTaskSnapshot]:
    """查看当前软件公司任务看板。"""
    return office_runtime.task_snapshots()


@app.post("/boss/command")
async def boss_command(command: BossCommand) -> list[AgentCommand]:
    """玩家 boss 下达指令，返回受影响员工的下一步行动。"""
    return await office_runtime.apply_boss_command(command)


@app.post("/agents/tick")
async def agents_tick(worker_ids: list[str] | None = Body(default=None)) -> list[AgentCommand]:
    """主动推进员工自主循环，适合网页或测试环境调用。"""
    return await office_runtime.autonomy_tick(worker_ids)


@app.websocket("/ws/office")
async def office_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            event = WorkerEvent.model_validate(data)
            commands = await office_runtime.handle_event(event)
            for command in commands:
                await websocket.send_json(command.model_dump())
    except WebSocketDisconnect:
        return
