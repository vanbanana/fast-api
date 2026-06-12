import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.directive_router import ROUTE_MEETING, ROUTE_WORK, fallback_route_directive
from app.domain import BossDirective


def verify_fallback_router() -> None:
    work_route = fallback_route_directive(
        BossDirective(
            text="讨论一下一个教育类项目怎么做，团队自己拆需求开发测试上线",
            priority=4,
            target_worker_ids=[],
        )
    )
    assert work_route.route == ROUTE_WORK
    assert not work_route.is_meeting

    meeting_route = fallback_route_directive(
        BossDirective(
            text="去会议室讨论一下一个教育类项目问题",
            priority=4,
            target_worker_ids=[],
        )
    )
    assert meeting_route.route == ROUTE_MEETING
    assert meeting_route.is_meeting


if __name__ == "__main__":
    verify_fallback_router()
    print("directive router checks passed")
