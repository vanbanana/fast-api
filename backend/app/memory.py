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
        self.compact_if_needed(worker_id)

    def should_store(self, text: str) -> bool:
        text = self._normalize_memory_text(text)
        if not text or self._is_debug_memory(text):
            return False
        if self._is_low_value_memory(text):
            return False
        return True

    def build_context(self, worker_id: str, query: str) -> str:
        """给 LLM 的压缩上下文，只放摘要、长期事实和少量相关近期事件。"""
        self.ensure_agent(worker_id, worker_id, "员工")
        global_memory = self._read_text(self.global_file, 1200)
        agent_memory = self._read_text(self._agent_dir(worker_id) / "long_term_memory.md", 1200)
        summary = self._read_text(self._agent_dir(worker_id) / "summary.md", 1400)
        events = self._select_events(worker_id, query, settings.memory_recent_events)
        event_lines = "\n".join(f"- [{item.get('kind', 'event')}] {item.get('text', '')}" for item in events)
        return (
            "【公司共享记忆】\n"
            f"{global_memory}\n\n"
            "【员工长期记忆】\n"
            f"{agent_memory}\n\n"
            "【压缩历史摘要】\n"
            f"{summary}\n\n"
            "【相关近期事件】\n"
            f"{event_lines or '暂无相关近期事件。'}"
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

    def compact_if_needed(self, worker_id: str) -> None:
        events = self._read_events(worker_id)
        visible_events = self._valuable_events(events)
        if not self._should_compact_by_context(worker_id, visible_events):
            return
        keep_count = max(5, settings.memory_keep_tail_events)
        older = visible_events[:-keep_count]
        tail = visible_events[-keep_count:]
        summary_file = self._agent_dir(worker_id) / "summary.md"
        existing_lines = self._summary_lines(summary_file)
        compact_lines = self._compress_events(older)
        summary_file.write_text(
            self._format_summary(existing_lines + compact_lines),
            encoding="utf-8",
        )
        with self._events_file(worker_id).open("w", encoding="utf-8") as handle:
            for item in tail:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._write_recent_markdown(worker_id)

    def _should_compact_by_context(self, worker_id: str, visible_events: list[dict[str, str]]) -> bool:
        context_text = "\n".join([
            self._read_text(self.global_file, settings.llm_context_window_tokens),
            self._read_text(self._agent_dir(worker_id) / "long_term_memory.md", settings.llm_context_window_tokens),
            self._read_text(self._agent_dir(worker_id) / "summary.md", settings.llm_context_window_tokens),
            "\n".join(str(item.get("text", "")) for item in visible_events),
        ])
        estimated_tokens = self._estimate_tokens(context_text)
        compact_at = int(settings.llm_context_window_tokens * settings.memory_compact_context_ratio)
        return estimated_tokens >= compact_at

    def _estimate_tokens(self, text: str) -> int:
        # 中文场景按 1 字约 1 token 保守估算；英文按 4 字符约 1 token。
        cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        non_cjk_count = max(0, len(text) - cjk_count)
        return cjk_count + non_cjk_count // 4

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

    def _compress_events(self, events: list[dict[str, str]]) -> list[str]:
        buckets: dict[str, list[str]] = {
            "目标": [],
            "风险": [],
            "协作": [],
            "完成": [],
            "其他": [],
        }
        for item in events:
            text = self._normalize_memory_text(str(item.get("text", "")))
            if not self.should_store(text):
                continue
            if "老板指令" in text or "领取任务" in text:
                buckets["目标"].append(text)
            elif "风险" in text:
                buckets["风险"].append(text)
            elif "协作" in text or "待确认" in text:
                buckets["协作"].append(text)
            elif "完成任务" in text:
                buckets["完成"].append(text)
            else:
                buckets["其他"].append(text)
        lines: list[str] = []
        for title, values in buckets.items():
            for value in self._dedupe_values(values)[-6:]:
                lines.append(f"- {title}: {value[:160]}")
        return self._dedupe_values(lines) or ["- 无可压缩事件。"]

    def _select_events(self, worker_id: str, query: str, limit: int) -> list[dict[str, str]]:
        events = self._read_events(worker_id)
        if not events:
            return []
        query_tokens = {char for char in query if char.strip()}
        scored: list[tuple[int, int, dict[str, str]]] = []
        for index, item in enumerate(events):
            text = str(item.get("text", ""))
            if self._is_debug_memory(text):
                continue
            score = sum(1 for char in query_tokens if char in text)
            recent_bonus = max(0, index - len(events) + limit)
            scored.append((score + recent_bonus, index, item))
        scored.sort(key=lambda value: (value[0], value[1]), reverse=True)
        selected = sorted(scored[:limit], key=lambda value: value[1])
        return [item for _score, _index, item in selected]

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
