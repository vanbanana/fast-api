import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.meeting_autogen import _clean_meeting_text


def verify_meeting_cleanup() -> None:
    assert _clean_meeting_text("这个最好同步一下，不然容易各做各的。") == ""
    assert _clean_meeting_text("<tool_call>{bad}") == ""
    assert _clean_meeting_text("小周确认登录接口错误码和返回字段。") == "小周确认登录接口错误码和返回字段。"
    assert len(_clean_meeting_text("字段" * 80)) <= 64


if __name__ == "__main__":
    verify_meeting_cleanup()
    print("meeting text cleanup checks passed")
