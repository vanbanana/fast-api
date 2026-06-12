# Office Multi-Agent Architecture

目标：后端必须是真实 multi-agent 编排，而不是多个 NPC 分别调用 LLM。

## 分层

1. Godot 协议层
   - 文件：`app/main.py`、Godot `office_demo.gd`
   - 职责：传输 `WorkerEvent` 和 `AgentCommand`
   - 不负责业务判断，不决定谁发言、不拆任务

2. Runtime 层
   - 文件：`app/runtime.py` 中的 `OfficeRuntime`
   - 职责：维护公司状态、路由事件、切换会议/工作模式、把 Godot 事件交给对应团队编排
   - 不应该直接生成复杂会议内容或项目拆解

3. AutoGen Teams 层
   - 文件：`app/meeting_autogen.py`、`app/team_autogen.py`
   - 职责：使用 AutoGen `RoundRobinGroupChat` 进行共享上下文的多 agent 编排
   - 会议：`OfficeMeetingAgent` 生成会议 transcript
   - 项目规划：`OfficePlanningAgent` 生成任务拆解

4. Planning Service 层
   - 文件：`app/planning_service.py`
   - 职责：选择规划参与人、调用 AutoGen 项目规划团队、把结构化规划项落到任务看板
   - Runtime 不直接处理任务拆解提示词或 fallback 文案

5. Meeting Runtime 层
   - 文件：`app/meeting_runtime.py`、`app/meeting_session.py`
   - 职责：选择参会人、分配会议座位、锁定参会人、播放会议发言、会议结束后派工
   - 工作模式不能调用会议座位；会议模式中参会人不能进入普通工作决策

6. Domain 层
   - 文件：`app/domain.py`
   - 职责：任务看板、公司状态、老板指令、Godot 目标点集合

7. Worker Agent 层
   - 文件：`app/worker_agent.py`、`app/worker_decision_policy.py`、`app/worker_rule_context.py`
   - 职责：员工画像加载、单个员工状态、员工行动决策、短期记忆入口
   - `worker_decision_policy.py` 承载固定工位、会议椅禁入、协作对象归一化等本地硬约束
   - `worker_rule_context.py` 承载规则决策文案、岗位辅助建议和确认问题
   - 员工提示词来自 `backend/prompts/agents/*.md`，不是写死在运行时代码里

8. Memory 层
   - 文件：`app/memory.py`、`backend/memory/**`
   - 职责：长期记忆、近期高价值事件、上下文窗口接近阈值时压缩
   - 空闲移动、到达点位、规则决策、调试流水不能进入记忆

## 事件流

### 会议

1. 玩家输入包含“讨论/开会/会议/评审/同步”
2. `OfficeRuntime.apply_boss_command()` 创建会议会话
3. Godot 收到 8 个静默入座 `move_to`
4. 所有人到达会议椅后，`meeting_autogen.run_round_robin_meeting()` 运行 AutoGen `RoundRobinGroupChat`
5. 后端保存 pending transcript
6. Godot 每显示完一句气泡，回传 `meeting_say_done`
7. 后端播放下一句

### 项目规划

1. 玩家输入普通项目目标
2. `team_autogen.run_project_planning()` 运行 AutoGen `RoundRobinGroupChat`
3. 项目经理、产品、后端、前端、UI、测试、架构、数据按共享上下文生成任务项
4. Runtime 将任务项落到公司任务看板
5. 只有被分配任务的员工收到行动命令

## 当前技术债

- `app/worker_agent.py` 仍然偏大，后续可继续拆出：
  - `worker_llm_decision.py`: LLM 决策提示词组装、工具结果清洗、可见思考流生成
- `app/worker_profile_loader.py` 已承载 Markdown 员工画像加载，不应再把画像解析逻辑写回 `worker_agent.py`。
- `app/agents.py` 只保留旧导入路径兼容，不应再新增业务逻辑。
- 拆分时不能改变 Godot WebSocket 协议。
