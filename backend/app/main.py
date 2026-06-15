import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.llm_client import llm_client
from app.runtime import office_runtime
from app.schemas import AgentCommand, AgentSnapshot, AtmosphereRequest as SchemaAtmosphereRequest, AtmosphereResponse as SchemaAtmosphereResponse, BossCommand, CompanySnapshot, ProjectTaskSnapshot, WorkerEvent
from app.atmosphere_service import generate as generate_atmosphere, AtmosphereRequest as ServiceAtmosphereRequest, AtmosphereResponse as ServiceAtmosphereResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Purr-formance Office Agents")

# CORS：允许 Web 前端跨域访问（Web 导出后可能从不同端口/域名访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/tasks/{worker_id}")
async def tasks_for_worker(worker_id: str) -> list[ProjectTaskSnapshot]:
    """查看某个员工的所有任务。"""
    return [t.snapshot() for t in office_runtime.company.tasks.values()
            if t.assignee_id == worker_id]


@app.post("/boss/command")
async def boss_command(command: BossCommand) -> list[AgentCommand]:
    """玩家 boss 下达指令，返回受影响员工的下一步行动。"""
    return await office_runtime.apply_boss_command(command)


@app.post("/atmosphere")
async def atmosphere(request: list[SchemaAtmosphereRequest]) -> list[SchemaAtmosphereResponse]:
    """批量生成氛围数据。Godot 每 10 秒左右请求一次，一次传多个 worker。"""
    results = []
    for item in request:
        req = ServiceAtmosphereRequest(
            worker_id=item.worker_id,
            name=item.name,
            role=item.role,
            personality=item.personality,
            state=item.state,
            location=item.location,
            nearby_workers=item.nearby_workers,
            last_event=item.last_event,
            current_task=item.current_task,
            energy=item.energy,
            stress=item.stress,
        )
        resp = await generate_atmosphere(req)
        results.append(SchemaAtmosphereResponse(
            say=resp.say,
            status=resp.status,
            mood=resp.mood,
            observation=resp.observation,
        ))
    return results


@app.get("/usage")
async def usage() -> dict[str, int]:
    """查看本次运行累计的 LLM token 消耗（来自官方 usage 字段）。"""
    return llm_client.usage_snapshot()


@app.websocket("/ws/office")
async def office_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    last_usage: dict[str, int] = {}
    try:
        while True:
            data = await websocket.receive_json()
            try:
                # 氛围请求：直接返回台词/状态/心情，不走 runtime 事件链
                if data.get("type") == "atmosphere_request":
                    payload_data = data.get("payload", {}) or {}
                    items = payload_data.get("workers", [])
                    if not isinstance(items, list):
                        items = []
                    results = []
                    for item in items:
                        schema_req = SchemaAtmosphereRequest.model_validate(item)
                        req = ServiceAtmosphereRequest(
                            worker_id=schema_req.worker_id,
                            name=schema_req.name,
                            role=schema_req.role,
                            personality=schema_req.personality,
                            state=schema_req.state,
                            location=schema_req.location,
                            nearby_workers=schema_req.nearby_workers,
                            last_event=schema_req.last_event,
                            current_task=schema_req.current_task,
                            energy=schema_req.energy,
                            stress=schema_req.stress,
                        )
                        resp = await generate_atmosphere(req)
                        results.append(SchemaAtmosphereResponse(
                            say=resp.say,
                            status=resp.status,
                            mood=resp.mood,
                            observation=resp.observation,
                        ).model_dump())
                    await websocket.send_json({"type": "atmosphere_response", "payload": results})
                    continue

                event = WorkerEvent.model_validate(data)
                commands = await office_runtime.handle_event(event)
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.exception("handle_event failed: %s", data)
                continue
            for command in commands:
                await websocket.send_json(command.model_dump())
            # 推送本次处理中产生的 LLM 调用日志到 Godot F12 面板
            llm_logs = llm_client.drain_log()
            for entry in llm_logs:
                await websocket.send_json({
                    "worker_id": "office",
                    "action": "llm_log",
                    "say": "",
                    "payload": entry,
                })
            current_usage = llm_client.usage_snapshot()
            if current_usage != last_usage:
                last_usage = current_usage
                await websocket.send_json({
                    "worker_id": "office",
                    "action": "token_usage",
                    "say": "",
                    "payload": current_usage,
                })
    except WebSocketDisconnect:
        return


# ---- 静态文件托管：Godot Web 导出文件 ----
# 将 Godot 导出的 HTML5 文件放到 backend/web/ 目录下，
# 启动后访问 http://localhost:8000/ 即可打开游戏。
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
