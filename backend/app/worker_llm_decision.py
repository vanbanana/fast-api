"""LLM 输出文本清洗工具。

只保留 clean_visible_text 供 meeting_runtime 使用。
其余 LLM 决策相关函数已随决策链一起移除。
"""


def clean_visible_text(value: object) -> str:
    """清洗 function calling 偶发的工具参数残片，避免玩家看到后台格式。"""
    text = _text_value(value).strip()
    for marker in ["<parameter=", "</parameter", "<tool_call", "</tool_call", "```"]:
        index = text.find(marker)
        if index >= 0:
            text = text[:index].strip()
    while "，，" in text:
        text = text.replace("，，", "，")
    while "。。" in text:
        text = text.replace("。。", "。")
    return text[:140]


def looks_like_tool_noise(text: str) -> bool:
    if not text:
        return False
    return "<parameter=" in text or "</" in text or text.count("，") > 12 or text.count(".") > 12


def _text_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)
