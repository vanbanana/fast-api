"""模拟工作日节奏的轻量时钟。

真实公司一天有明显节奏：早会对齐 -> 上午专注 -> 午休 -> 下午推进 ->
傍晚收尾。时钟按 autonomy tick 推进，输出当前时段和行为偏置，让
agent 的休息/闲逛/工作概率随时段变化，而不是全天均匀随机。
"""
from dataclasses import dataclass


PHASE_STANDUP = "morning_standup"
PHASE_FOCUS = "deep_focus"
PHASE_LUNCH = "lunch_break"
PHASE_AFTERNOON = "afternoon_push"
PHASE_WRAPUP = "wrap_up"

_PHASE_ORDER = [
    (PHASE_STANDUP, 6),
    (PHASE_FOCUS, 30),
    (PHASE_LUNCH, 10),
    (PHASE_AFTERNOON, 30),
    (PHASE_WRAPUP, 10),
]

_PHASE_LABELS = {
    PHASE_STANDUP: "上午刚开工，大家在对齐今天的安排",
    PHASE_FOCUS: "上午专注时段，办公室比较安静",
    PHASE_LUNCH: "午休时间，可以吃饭、休息、闲聊",
    PHASE_AFTERNOON: "下午推进时段，任务在收敛",
    PHASE_WRAPUP: "临近下班，大家在收尾和同步进展",
}

# 各时段对规则行为的偏置倍率
_PHASE_BIAS = {
    PHASE_STANDUP: {"break": 0.3, "roam": 1.4},
    PHASE_FOCUS: {"break": 0.5, "roam": 0.5},
    PHASE_LUNCH: {"break": 4.0, "roam": 2.0},
    PHASE_AFTERNOON: {"break": 1.0, "roam": 0.8},
    PHASE_WRAPUP: {"break": 1.6, "roam": 1.5},
}

_DAY_TICKS = sum(length for _, length in _PHASE_ORDER)


@dataclass
class OfficeClock:
    tick: float = 0.0
    day: int = 1

    def advance(self, amount: float = 1.0) -> None:
        """WS 事件是按员工逐个到达的，可以用分数步长近似一轮。"""
        self.tick += max(0.0, amount)
        if self.tick >= _DAY_TICKS:
            self.tick = 0.0
            self.day += 1

    def phase(self) -> str:
        cursor = 0
        for phase, length in _PHASE_ORDER:
            cursor += length
            if self.tick < cursor:
                return phase
        return PHASE_WRAPUP

    def phase_label(self) -> str:
        return _PHASE_LABELS[self.phase()]

    def break_bias(self) -> float:
        return _PHASE_BIAS[self.phase()]["break"]

    def roam_bias(self) -> float:
        return _PHASE_BIAS[self.phase()]["roam"]

    def reset(self) -> None:
        self.tick = 0.0
        self.day = 1


office_clock = OfficeClock()
