# Agent 记忆文件

这个目录参考 NanoClaw 的层级记忆思路：全局记忆 + 每个 agent 自己的记忆目录。

## 目录

- `company/CLAUDE.md`: 公司共享记忆，所有员工都会读。
- `agents/{worker_id}_{name}/CLAUDE.md`: 员工长期记忆，可以手动编辑。
- `agents/{worker_id}_{name}/summary.md`: 接近上下文上限时才生成的历史摘要。
- `agents/{worker_id}_{name}/recent_events.md`: 给人看的近期事件。
- `agents/{worker_id}_{name}/events.jsonl`: 给程序读取的事件流水。

## 管理方式

- 想改某个员工长期习惯，编辑他的 `CLAUDE.md`。
- 想看他最近在干嘛，打开 `recent_events.md`。
- 只有业务目标、风险、协作、完成情况等高价值事件会进入记忆。
- 空闲移动、到达点位、规则决策、调试流水不会进入记忆文件。
- 记忆压缩按 MiMo 1M 上下文窗口估算，默认接近 80% 才压缩，不按事件条数压缩。
