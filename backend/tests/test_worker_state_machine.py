import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.worker_state import PendingReply, WorkerState, WorkerStateMachine


def verify_state_machine() -> None:
    fsm = WorkerStateMachine()
    assert fsm.state == WorkerState.IDLE

    assert fsm.transition(WorkerState.WORKING)
    assert fsm.state == WorkerState.WORKING

    # 找同事 -> 当面沟通 -> 回去干活
    assert fsm.start_seeking("worker2")
    assert fsm.state == WorkerState.SEEKING_COLLEAGUE
    assert fsm.helper_id == "worker2"
    assert fsm.transition(WorkerState.COLLABORATING)
    # 离开 SEEKING 时自动清理找人状态
    assert fsm.helper_id == ""
    assert not fsm.checked_helper_desk
    assert fsm.transition(WorkerState.WORKING)

    # 非法迁移被拒绝且状态不变：会议中不能直接跑去找同事
    fsm.force(WorkerState.MEETING)
    assert not fsm.transition(WorkerState.SEEKING_COLLEAGUE)
    assert not fsm.transition(WorkerState.RESTING)
    assert fsm.state == WorkerState.MEETING
    assert fsm.transition(WorkerState.WORKING)

    # 同状态迁移恒为合法
    assert fsm.transition(WorkerState.WORKING)

    fsm.pending_reply = PendingReply(from_worker_id="worker1", say="好的")
    assert not fsm.pending_reply.is_empty()
    fsm.reset()
    assert fsm.state == WorkerState.IDLE
    assert fsm.pending_reply.is_empty()


if __name__ == "__main__":
    verify_state_machine()
    print("worker state machine checks passed")
