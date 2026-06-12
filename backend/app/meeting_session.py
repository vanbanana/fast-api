from dataclasses import dataclass, field


@dataclass
class MeetingSession:
    """会议运行态：座位分配、到齐状态、待播放发言和会议记录。"""

    session_id: str
    topic: str
    participant_ids: list[str]
    seats_by_worker: dict[str, str]
    seated_worker_ids: set[str] = field(default_factory=set)
    transcript: list[dict[str, str]] = field(default_factory=list)
    pending_turns: list[dict[str, str]] = field(default_factory=list)
    turns_task: object | None = None
    max_turns: int = 10
    is_started: bool = False

    def seat_for(self, worker_id: str) -> str:
        return self.seats_by_worker.get(worker_id, "")

    def mark_arrived(self, worker_id: str, target_id: str) -> bool:
        if self.seat_for(worker_id) != target_id:
            return False
        self.seated_worker_ids.add(worker_id)
        return True

    def is_ready(self) -> bool:
        return bool(self.participant_ids) and all(worker_id in self.seated_worker_ids for worker_id in self.participant_ids)

    def has_more_turns(self) -> bool:
        return bool(self.pending_turns)

    def append_turn(self, worker_id: str, speaker: str, text: str) -> None:
        self.transcript.append({"worker_id": worker_id, "speaker": speaker, "text": text})

    def pop_turn(self) -> dict[str, str]:
        if not self.pending_turns:
            return {}
        turn = self.pending_turns.pop(0)
        self.transcript.append(turn)
        return turn
