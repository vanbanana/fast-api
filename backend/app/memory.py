import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MemoryEvent:
    """单条员工记忆事件，写入 JSONL，便于以后换 SQLite 或向量检索。"""

    ts: str
    worker_id: str
    text: str
    kind: str = "event"

    def model_dump(self) -> dict[str, str]:
        return {
            "ts": self.ts,
            "worker_id": self.worker_id,
            "kind": self.kind,
            "text": self.text,
        }


class AgentMemoryStore:
    """NanoClaw 风格的轻量层级记忆。

    目录结构：
    memory/
      README.md                         记忆目录说明
      company/company_memory.md         全公司共享背景
      agents/{worker_id}_{name}/
        long_term_memory.md             员工固定偏好和长期事实
        summary.md                      压缩后的历史摘要
        recent_events.md                方便人工阅读的近期事件
        events.jsonl                    程序读取的原始事件
    """

    def __init__(self) -> None:
        self.root = self._resolve_memory_dir()
        self.company_dir = self.root / "company"
        self.global_file = self.company_dir / "company_memory.md"
        self.agents_dir = self.root / "agents"
        self._agents: dict[str, dict[str, str | list[str]]] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self.company_dir.mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_files()
        self._ensure_readme()
        if not self.global_file.exists():
            self.global_file.write_text(
                "# 公司共享记忆\n\n"
                "- 玩家只输入一次初始业务目标。\n"
                "- 团队内部自行完成需求补全、任务拆解、协作确认和上线推进。\n",
                encoding="utf-8",
            )

    def _resolve_memory_dir(self) -> Path:
        raw_path = Path(settings.agent_memory_dir)
        if raw_path.is_absolute():
            return raw_path
        backend_root = Path(__file__).resolve().parents[1]
        return backend_root / raw_path

    def ensure_agent(self, worker_id: str, name: str, role: str) -> None:
        agent_dir = self._agent_dir(worker_id, name)
        agent_dir.mkdir(parents=True, exist_ok=True)
        self._remember_agent_dir(worker_id, agent_dir)
        profile_file = agent_dir / "long_term_memory.md"
        summary_file = agent_dir / "summary.md"
        recent_file = agent_dir / "recent_events.md"
        events_file = agent_dir / "events.jsonl"
        if not profile_file.exists():
            profile_file.write_text(
                f"# {name} / {role}\n\n"
                "- 这是该员工的长期记忆文件，可以手动编辑。\n"
                "- 只记录稳定偏好、长期关系、反复出现的问题和工作习惯。\n",
                encoding="utf-8",
            )
        if not summary_file.exists():
            summary_file.write_text("# 压缩摘要\n\n暂无历史摘要。\n", encoding="utf-8")
        if not recent_file.exists():
            recent_file.write_text("# 近期事件\n\n暂无近期事件。\n", encoding="utf-8")
        if not events_file.exists():
            events_file.write_text("", encoding="utf-8")

    def register_agent(self, worker_id: str, profile: dict[str, str | list[str]]) -> None:
        """注册 agent 属性到内存字典，供 build_context 的 Core Memory 使用。"""
        self._agents[worker_id] = dict(profile)

    def remember(self, worker_id: str, text: str, kind: str = "event") -> None:
        text = self._normalize_memory_text(text)
        if not worker_id or not text:
            return
        if not self.should_store(text):
            return
        self.ensure_agent(worker_id, worker_id, "员工")
        if self._is_recent_duplicate(worker_id, text):
            return
        event = MemoryEvent(ts=_utc_now(), worker_id=worker_id, kind=kind, text=text)
        with self._events_file(worker_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")
        self._write_recent_markdown(worker_id)

    def should_store(self, text: str) -> bool:
        """写入前过滤：只存对'未来决策有影响'的信息。"""
        if not text or len(text.strip()) < 4:
            return False
        skip_prefixes = [
            "规则决策:", "茶水间闲聊:", "在工位整理状态",
            "短暂休息恢复", "留在本人固定工位", "继续处理",
            "暂停移动", "自主循环达到上限", "整理记忆",
        ]
        text_stripped = text.strip()
        for prefix in skip_prefixes:
            if text_stripped.startswith(prefix):
                return False
        return True

    def build_context(self, worker_id: str, query: str = "") -> str:
        """给 LLM 的上下文：Core Memory（始终在）+ Working Memory（最近有价值事件）。

        Core Memory 从 agent profile 直接生成，不读磁盘文件。
        Working Memory 从内存列表取最近 N 条，不读 JSONL。
        Archival（events.jsonl）不参与上下文组装。
        """
        self.ensure_agent(worker_id, worker_id, "员工")

        # Core Memory：从 agent 属性生成（~400字）
        agent = self._agents.get(worker_id)
        if agent:
            core_lines = [
                f"身份：{agent.get('name', '?')}，{agent.get('role', '?')}",
                f"性格：{agent.get('personality', '?')}",
                f"工作方式：{agent.get('work_style', '?')}",
                f"沟通风格：{agent.get('communication_style', '?')}",
                f"重视：{'、'.join(agent.get('work_values', []))}",
            ]
            # 加入长期记忆文件的补充信息（如果有的话）
            lt_mem = self._read_text(self._agent_dir(worker_id) / "long_term_memory.md", 400)
            if lt_mem and lt_mem != f"# {agent.get('name', '?')}":
                core_lines.append(f"个人档案补充：{lt_mem[:300]}")
            core_memory = "\n".join(core_lines)
        else:
            core_memory = "暂无角色档案。"

        # Working Memory：从内存列表取最近有价值的事件（不读 JSONL）
        events = self._read_events(worker_id)
        valuable_events = [e for e in events[-12:] if self._is_valuable_event(e)]
        event_lines = "\n".join(f"- {e.get('text', '')}" for e in valuable_events[-6:])

        return (
            f"【你的角色】\n{core_memory}\n\n"
            f"【近期重要事件】\n{event_lines or '暂无特别事件。'}"
        )

    def display_memory(self, worker_id: str, limit: int = 6) -> list[str]:
        events = self._read_events(worker_id)
        visible: list[str] = []
        for item in reversed(events):
            text = str(item.get("text", ""))
            if self._is_debug_memory(text):
                continue
            visible.append(text.replace("工作记忆:", ""))
            if len(visible) >= limit:
                break
        return list(reversed(visible))

    @staticmethod
    def _is_valuable_event(item: dict[str, str]) -> bool:
        """判断事件是否有存储价值。过滤掉闲聊、纯移动、idle 决策记录。"""
        text = str(item.get("text", ""))
        kind = str(item.get("kind", "event"))
        # 过滤低价值事件
        low_value_patterns = [
            "茶水间闲聊:",
            "规则决策:",       # 无意义的每步决策记录
            "在工位整理状态",
            "短暂休息恢复状态",
            "留在本人固定工位",
            "继续处理",
            "暂停移动",
            "自主循环达到上限",
            "整理记忆和任务优先级",
        ]
        if any(p in text for p in low_value_patterns):
            return False
        if kind in ("chat", "movement"):
            return False
        return bool(text.strip())

    def _ensure_readme(self) -> None:
        readme = self.root / "README.md"
        if readme.exists():
            return
        readme.write_text(
            "# Agent 记忆文件\n\n"
            "这个目录参考 NanoClaw 的层级记忆思路：全局记忆 + 每个 agent 自己的记忆目录。\n\n"
            "## 目录\n\n"
            "- `company/company_memory.md`: 公司共享记忆，所有员工都会读。\n"
            "- `agents/{worker_id}_{name}/long_term_memory.md`: 员工长期记忆，可以手动编辑。\n"
            "- `agents/{worker_id}_{name}/summary.md`: 自动压缩后的历史摘要。\n"
            "- `agents/{worker_id}_{name}/recent_events.md`: 给人看的近期事件。\n"
            "- `agents/{worker_id}_{name}/events.jsonl`: 给程序读取的事件流水。\n\n"
            "## 管理方式\n\n"
            "- 想改某个员工长期习惯，编辑他的 `long_term_memory.md`。\n"
            "- 想看他最近在干嘛，打开 `recent_events.md`。\n"
            "- 事件太多时，后端会把旧事件压缩进 `summary.md`，只保留尾部事件。\n",
            encoding="utf-8",
        )

    def _migrate_legacy_files(self) -> None:
        legacy_global = self.root / "CLAUDE.md"
        if legacy_global.exists() and not self.global_file.exists():
            self.global_file.write_text(legacy_global.read_text(encoding="utf-8"), encoding="utf-8")
            legacy_global.unlink()
        legacy_company = self.company_dir / "CLAUDE.md"
        if legacy_company.exists() and not self.global_file.exists():
            self.global_file.write_text(legacy_company.read_text(encoding="utf-8"), encoding="utf-8")
            legacy_company.unlink()
        elif legacy_company.exists():
            legacy_company.unlink()
        for agent_dir in self.agents_dir.glob("*"):
            if not agent_dir.is_dir():
                continue
            legacy_agent = agent_dir / "CLAUDE.md"
            new_agent = agent_dir / "long_term_memory.md"
            if legacy_agent.exists() and not new_agent.exists():
                new_agent.write_text(legacy_agent.read_text(encoding="utf-8"), encoding="utf-8")
                legacy_agent.unlink()

    def _read_events(self, worker_id: str) -> list[dict[str, str]]:
        events_file = self._events_file(worker_id)
        if not events_file.exists():
            return []
        events: list[dict[str, str]] = []
        for line in events_file.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
        return events

    def _write_recent_markdown(self, worker_id: str) -> None:
        agent_dir = self._agent_dir(worker_id)
        events = self._read_events(worker_id)
        visible = self._valuable_events(events)
        lines = ["# 近期事件", ""]
        for item in visible[-40:]:
            ts = str(item.get("ts", ""))
            kind = str(item.get("kind", "event"))
            text = self._normalize_memory_text(str(item.get("text", ""))).replace("工作记忆:", "")
            lines.append(f"- `{ts}` [{kind}] {text}")
        if len(lines) == 2:
            lines.append("暂无近期事件。")
        (agent_dir / "recent_events.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _read_text(self, path: Path, max_chars: int) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")[:max_chars]

    def _agent_dir(self, worker_id: str, name: str | None = None) -> Path:
        mapping_file = self.root / ".agent_dirs.json"
        mapping: dict[str, str] = {}
        if mapping_file.exists():
            try:
                loaded = json.loads(mapping_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    mapping = {str(key): str(value) for key, value in loaded.items()}
            except json.JSONDecodeError:
                mapping = {}
        if worker_id in mapping:
            return self.root / mapping[worker_id]
        safe_name = self._safe_path_part(name or worker_id)
        return self.agents_dir / f"{worker_id}_{safe_name}"

    def _remember_agent_dir(self, worker_id: str, agent_dir: Path) -> None:
        mapping_file = self.root / ".agent_dirs.json"
        mapping: dict[str, str] = {}
        if mapping_file.exists():
            try:
                loaded = json.loads(mapping_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    mapping = {str(key): str(value) for key, value in loaded.items()}
            except json.JSONDecodeError:
                mapping = {}
        mapping[worker_id] = str(agent_dir.relative_to(self.root)).replace("\\", "/")
        mapping_file.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_path_part(self, value: str) -> str:
        blocked = '<>:"/\\|?*'
        safe = "".join("_" if char in blocked else char for char in value.strip())
        return safe or "agent"

    def _events_file(self, worker_id: str) -> Path:
        return self._agent_dir(worker_id) / "events.jsonl"

    def _is_debug_memory(self, text: str) -> bool:
        return (
            "LLM目标无效" in text
            or "<parameter=" in text
            or "</" in text
            or "，，，" in text
            or text.startswith("LLM决策:")
            or text.startswith("规则决策:")
            or text.startswith("worker_arrived:")
            or text.startswith("worker_ready:")
            or text.startswith("scene_ready:")
            or text.startswith("boss_command:")
        )

    def _is_low_value_memory(self, text: str) -> bool:
        stripped = text.replace("工作记忆:", "").strip()
        if "老板指令:" in stripped:
            directive_text = stripped.split("老板指令:", 1)[1].replace("？", "?").strip()
            if directive_text and set(directive_text) <= {"?"}:
                return True
        compact = stripped.replace("？", "?").replace(" ", "")
        if compact and compact.count("?") >= max(2, len(compact) // 2):
            return True
        low_value_prefixes = [
            "空闲移动到",
            "决定去 desk",
            "决定去 office",
            "决定去 worker",
            "决定去 kitchen",
            "决定去 water",
            "决定去 left",
            "决定去 right",
        ]
        if any(stripped.startswith(prefix) for prefix in low_value_prefixes):
            return True
        if "等待明确目标" in stripped and ("移动到" in stripped or "决定去" in stripped):
            return True
        return False

    def _normalize_memory_text(self, text: str) -> str:
        normalized = " ".join(text.strip().split())
        while "风险: 风险:" in normalized:
            normalized = normalized.replace("风险: 风险:", "风险:")
        while "风险:风险:" in normalized:
            normalized = normalized.replace("风险:风险:", "风险:")
        while "工作记忆: 工作记忆:" in normalized:
            normalized = normalized.replace("工作记忆: 工作记忆:", "工作记忆:")
        while "工作记忆:工作记忆:" in normalized:
            normalized = normalized.replace("工作记忆:工作记忆:", "工作记忆:")
        return normalized

    def _is_recent_duplicate(self, worker_id: str, text: str) -> bool:
        normalized = self._normalize_memory_text(text)
        for item in self._read_events(worker_id)[-40:]:
            if self._normalize_memory_text(str(item.get("text", ""))) == normalized:
                return True
        return False

    def _valuable_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        visible: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in events:
            text = self._normalize_memory_text(str(item.get("text", "")))
            if not self.should_store(text) or text in seen:
                continue
            copied = dict(item)
            copied["text"] = text
            visible.append(copied)
            seen.add(text)
        return visible

    def _dedupe_values(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = self._normalize_memory_text(value)
            if normalized in seen:
                continue
            deduped.append(normalized)
            seen.add(normalized)
        return deduped

    def _summary_lines(self, summary_file: Path) -> list[str]:
        if not summary_file.exists():
            return []
        lines: list[str] = []
        for line in summary_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            content = stripped[2:]
            bucket = "其他"
            text = content
            if ": " in content:
                bucket, text = content.split(": ", 1)
            text = self._normalize_memory_text(text)
            if self.should_store(text):
                lines.append(f"- {bucket}: {text}")
        return self._dedupe_values(lines)

    def _format_summary(self, lines: list[str]) -> str:
        clean_lines = self._dedupe_values([self._format_summary_line(line) for line in lines if line.strip()])
        if not clean_lines:
            return "# 压缩摘要\n\n暂无历史摘要。\n"
        return "# 压缩摘要\n\n## 自动整理 %s\n%s\n" % (_utc_now(), "\n".join(clean_lines[-80:]))

    def _format_summary_line(self, line: str) -> str:
        normalized = line.strip()
        if normalized.startswith("- ") and ": " not in normalized and ":" in normalized:
            head, tail = normalized.split(":", 1)
            normalized = f"{head}: {tail.strip()}"
        for bucket in ["目标", "风险", "协作", "完成", "其他"]:
            doubled = f"- {bucket}: {bucket}:"
            if normalized.startswith(doubled):
                normalized = f"- {bucket}: {normalized[len(doubled):].strip()}"
        return normalized


memory_store = AgentMemoryStore()
