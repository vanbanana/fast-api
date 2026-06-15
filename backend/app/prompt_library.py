"""集中式提示词库。

所有系统提示词、用户消息模板和限制文案都放在 backend/prompts/system/
下的文本文件里，代码只负责加载和填充槽位。改提示词不需要改代码。
"""
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "system"


@lru_cache(maxsize=64)
def _load(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip()


def render(template_name: str, **slots: object) -> str:
    """加载模板并填充 {slot} 槽位。"""
    text = _load(template_name)
    if slots:
        return text.format(**slots)
    return text


@lru_cache(maxsize=8)
def load_lines(name: str) -> tuple[str, ...]:
    """加载一行一条的限制文案列表（如会议禁用空话）。"""
    text = _load(name)
    return tuple(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))


def reload() -> None:
    """运行中修改提示词文件后调用，清空缓存。"""
    _load.cache_clear()
    load_lines.cache_clear()
