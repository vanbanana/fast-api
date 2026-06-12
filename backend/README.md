# Office Multi-Agent Backend

轻量 FastAPI 后端，用 WebSocket 给 Godot 办公室 demo 下发角色行为。

当前方案不把椅子、闲逛点坐标或 Marker 名称写死在后端。Godot 连接成功后会把 `seat_markers`、`idle_markers` 分组里的 Marker 同步给后端，后端只根据这些场景数据给角色返回目标 ID。

每个员工都有独立的人设、职责、工作方式、当前任务、心情、精力、短期记忆和自主循环计数。玩家作为 boss 只需要下达最初业务目标；需求补全、验收拆解、技术澄清、接口确认和协作沟通都由游戏内员工自行完成。`MAX_AUTONOMY_STEPS` 用来限制单个员工连续自主循环的最大次数，达到上限后会短暂冷却，避免死循环。

`LLM_DECISION_CHANCE` 控制每次决策调用 Mimo 的概率，默认 `0.35`。没有命中 LLM 或 LLM 返回不可用时，会走本地规则决策，保证游戏不会因为接口慢或失败而停住。

公司层面维护一个软件项目和任务看板。boss 指令会生成真实任务，任务会分配给对应员工；员工回到自己的工位或被 `/agents/tick` 推进时，会消耗精力、增加压力并推进任务进度。任务完成后会写入员工记忆，并影响公司士气和发布风险。

员工画像不止包含岗位，还包含沟通风格、工作价值观、冲突触发点和同事关系。agent 输出也不只是“去哪”，还会附带 `work_context`：

```json
{
  "intent": "为什么这样行动",
  "work_update": "对任务的实际推进或判断",
  "risk_note": "发现的风险",
  "needs_help_from": "需要协作的员工ID",
  "confirmation_question": "需要游戏内负责人或同事确认的问题",
  "confidence": 0.78
}
```

这些字段会进入员工记忆、任务备注和调试快照，后续可以直接显示在网页面板或 Godot 对话气泡里。

## 会议编排

讨论、开会、评审、同步等老板指令会进入会议会话，不走普通员工各自决策。

会议会话使用 AutoGen `RoundRobinGroupChat` 生成共享会议记录：每个员工会被适配成一个 AutoGen `ChatAgent`，在同一段议题和历史里轮流发言。后端只额外维护座位、到齐状态和待播放发言队列。Godot 负责角色移动和气泡展示；每句会议发言显示完后，Godot 回传 `meeting_say_done`，后端再播放下一位发言。

这样会议是一个共享上下文里的轮流讨论，不是 8 个员工各自生成一句互不相关的话。

## 项目规划编排

非会议类老板目标会先进入 AutoGen `RoundRobinGroupChat` 项目规划队列。项目经理、产品、后端、前端、UI、测试、架构、数据等员工会作为独立 `ChatAgent` 在同一个目标和历史上下文里轮流补充任务。后端把这些规划结果落成任务看板，再只让被分配到任务的员工行动。

因此普通项目目标不会再变成“所有人复制同一个任务”，而是拆成产品验收、接口/服务边界、前端状态、设计、测试、架构风险、数据指标等真实软件团队工作项。

## 员工提示词

所有员工画像和完整角色扮演提示词都在本地 Markdown：

```text
backend/prompts/agents/worker1.md
backend/prompts/agents/worker2.md
...
backend/prompts/agents/worker11.md
```

每个文件前半部分是可解析字段：

```text
---
worker_id: worker2
name: 小周
role: 后端工程师
personality: 专注、话少
work_style: 先看日志和接口契约，再改代码
communication_style: 简短、偏技术细节
work_values: 稳定性|可复现问题|接口边界
conflict_triggers: 需求频繁变化|没有复现步骤
relationship_notes: worker6=需要测试复现步骤|worker9=经常互相确认接口字段
---
```

`---` 后面的正文就是这个员工实际用于 LLM 的角色扮演提示词。修改 Markdown 后重启 FastAPI 后端即可生效。提示词目录也可以通过 `.env` 的 `AGENT_PROFILES_DIR` 改成别的位置。

## 启动

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 在 .env 里填 MIMO_API_KEY；MIMO_BASE_URL 和 MIMO_MODEL 已有默认值
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## WebSocket

```text
ws://127.0.0.1:8000/ws/office
```

## REST 调试

查看员工状态：

```text
GET http://127.0.0.1:8000/agents
```

查看公司总览：

```text
GET http://127.0.0.1:8000/company
```

查看任务看板：

```text
GET http://127.0.0.1:8000/tasks
```

玩家 boss 下达指令：

```text
POST http://127.0.0.1:8000/boss/command
```

```json
{"text":"所有人准备开需求评审会","priority":4}
```

只指挥某个员工：

```json
{"text":"小周回工位修登录 bug","target_worker_ids":["worker2"],"priority":5}
```

主动推进所有员工自主循环：

```text
POST http://127.0.0.1:8000/agents/tick
```

只推进部分员工：

```json
["worker1","worker2"]
```

Godot 上报：

```json
{"type":"world_snapshot","worker_id":"office","payload":{"targets":[{"id":"leftTopChair","group":"seat_markers"},{"id":"water1","group":"idle_markers"}]}}
```

```json
{"type":"worker_arrived","worker_id":"worker3","target_id":"leftTopChair","target_group":"seat_markers"}
```

后端下发：

```json
{"type":"command","worker_id":"worker3","action":"move_to","target_id":"water1","say":"我去倒杯水。"}
```

Godot 里已经有运行时创建的 boss 输入框，也可以在脚本里调用 `OfficeDemo.send_boss_command("所有人准备开会")`，它会通过 WebSocket 把指令交给后端。
