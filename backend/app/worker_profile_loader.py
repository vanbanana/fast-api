from pathlib import Path

from app.config import settings
from app.memory import memory_store
from app.worker_agent import OfficeAgent


def make_agent(
    worker_id: str,
    name: str,
    role: str,
    personality: str,
    work_style: str,
    communication_style: str,
    work_values: list[str],
    conflict_triggers: list[str],
    relationship_notes: dict[str, str] | None = None,
    roleplay_template: str = "",
) -> OfficeAgent:
    """集中创建员工画像，避免运行时散落硬编码字段。"""
    return OfficeAgent(
        worker_id=worker_id,
        name=name,
        role=role,
        personality=personality,
        work_style=work_style,
        communication_style=communication_style,
        work_values=work_values,
        conflict_triggers=conflict_triggers,
        relationship_notes=relationship_notes or {},
        roleplay_template=roleplay_template,
    )


def load_agent_profiles() -> dict[str, OfficeAgent]:
    """从本地 Markdown 加载员工画像和提示词，便于快速管理。"""
    profiles_dir = _resolve_profiles_dir()
    agents: dict[str, OfficeAgent] = {}
    for path in sorted(profiles_dir.glob("*.md"), key=_profile_sort_key):
        agent = _load_agent_profile(path)
        agents[agent.worker_id] = agent
        memory_store.ensure_agent(agent.worker_id, agent.name, agent.role)
        # 注册角色属性到 Core Memory，供 atmosphere 提示词使用
        memory_store.register_agent(agent.worker_id, {
            "name": agent.name,
            "role": agent.role,
            "personality": agent.personality,
            "work_style": agent.work_style,
            "communication_style": agent.communication_style,
            "work_values": agent.work_values,
        })
    return agents


def _profile_sort_key(path: Path) -> tuple[str, int]:
    stem = path.stem
    prefix = stem.rstrip("0123456789")
    suffix = stem[len(prefix) :]
    return prefix, int(suffix) if suffix.isdigit() else 0


def _resolve_profiles_dir() -> Path:
    raw_path = Path(settings.agent_profiles_dir)
    if raw_path.is_absolute():
        return raw_path
    backend_root = Path(__file__).resolve().parents[1]
    return backend_root / raw_path


def _load_agent_profile(path: Path) -> OfficeAgent:
    text = path.read_text(encoding="utf-8")
    meta, body = _split_markdown_profile(text)
    worker_id = meta.get("worker_id", path.stem)
    return make_agent(
        worker_id=worker_id,
        name=meta.get("name", worker_id),
        role=meta.get("role", "员工"),
        personality=meta.get("personality", "普通，正在适应团队节奏"),
        work_style=meta.get("work_style", "根据现场情况行动"),
        communication_style=meta.get("communication_style", "谨慎、先确认再行动"),
        work_values=_split_list(meta.get("work_values", "明确任务|减少返工")),
        conflict_triggers=_split_list(meta.get("conflict_triggers", "上下文不足|目标频繁变化")),
        relationship_notes=_split_relationships(meta.get("relationship_notes", "")),
        roleplay_template=body.strip(),
    )


def _split_markdown_profile(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _split_relationships(value: str) -> dict[str, str]:
    relationships: dict[str, str] = {}
    for item in _split_list(value):
        if "=" not in item:
            continue
        key, note = item.split("=", 1)
        relationships[key.strip()] = note.strip()
    return relationships
