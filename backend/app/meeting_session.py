from dataclasses import dataclass, field
from enum import Enum


class MeetingPhase(str, Enum):
    """会议阶段状态机。"""
    GATHERING = "gathering"       # 正在前往会议室
    SEATED = "seated"             # 已全部入座，准备开始
    DISCUSSING = "discussing"      # 讨论中
    CLOSING = "closing"           # 主管已提议结束，正在输出PRD总结
    FINISHED = "finished"         # 会议结束


@dataclass
class MeetingSession:
    """会议运行态：座位分配、到齐状态、逐轮讨论、PRD 产出。"""

    session_id: str
    topic: str
    participant_ids: list[str]
    seats_by_worker: dict[str, str]
    lead_worker_id: str = ""          # 主管/项目经理 ID，有权结束会议
    phase: MeetingPhase = MeetingPhase.GATHERING
    seated_worker_ids: set[str] = field(default_factory=set)
    transcript: list[dict[str, str]] = field(default_factory=list)
    prd_points: list[str] = field(default_factory=list)   # 每轮积累的 PRD 要点
    prd_final: str = ""               # 最终 PRD 总结
    current_turn: int = 0
    max_turns: int = 8                # 硬上限（8人约1圈，快速演示用）
    turns_task: object | None = None
    # 关闭阶段状态
    closing_prd_said: bool = False       # 主管是否已输出PRD总结
    acknowledged_ids: set[str] = field(default_factory=set)  # 已回复"收到"的人
    lead_prd_pending: bool = False      # 主管正在等待 LLM 生成 PRD（两阶段加载）

    def seat_for(self, worker_id: str) -> str:
        return self.seats_by_worker.get(worker_id, "")

    def mark_arrived(self, worker_id: str, target_id: str) -> bool:
        if self.seat_for(worker_id) != target_id:
            return False
        self.seated_worker_ids.add(worker_id)
        return True

    def is_ready(self) -> bool:
        """所有人都到了吗？"""
        return bool(self.participant_ids) and all(
            wid in self.seated_worker_ids for wid in self.participant_ids
        )

    def start_discussion(self) -> None:
        """所有人到齐 → 进入讨论阶段。"""
        self.phase = MeetingPhase.DISCUSSING

    def is_discussing(self) -> bool:
        return self.phase == MeetingPhase.DISCUSSING

    def is_closing(self) -> bool:
        return self.phase == MeetingPhase.CLOSING

    def start_closing(self, prd_summary: str = "") -> None:
        """主管提议结束 → 进入关闭阶段（先出PRD，再等大家确认）。"""
        self.phase = MeetingPhase.CLOSING
        if prd_summary:
            self.prd_final = prd_summary
        elif self.prd_points:
            self.prd_final = "\n".join(self.prd_points)

    def mark_acknowledged(self, worker_id: str) -> None:
        self.acknowledged_ids.add(worker_id)

    def all_acknowledged(self) -> bool:
        non_lead = [wid for wid in self.participant_ids if wid != self.lead_worker_id]
        return bool(non_lead) and all(wid in self.acknowledged_ids for wid in non_lead)

    def pending_ack_ids(self) -> list[str]:
        return [wid for wid in self.participant_ids
                if wid != self.lead_worker_id and wid not in self.acknowledged_ids]

    def is_finished(self) -> bool:
        return self.phase == MeetingPhase.FINISHED

    def finish(self, prd_summary: str = "") -> None:
        """会议结束，记录最终 PRD。"""
        self.phase = MeetingPhase.FINISHED
        if prd_summary:
            self.prd_final = prd_summary
        elif self.prd_points:
            self.prd_final = "\n".join(self.prd_points)

    def has_more_turns(self) -> bool:
        return self.current_turn < self.max_turns and not self.is_finished()

    def record_turn(self, worker_id: str, speaker_name: str, text: str,
                    done: bool = False, prd_point: str = "") -> None:
        """记录一轮发言。"""
        self.transcript.append({
            "worker_id": worker_id,
            "speaker": speaker_name,
            "text": text,
            "turn": self.current_turn,
        })
        if prd_point:
            self.prd_points.append(prd_point)
        self.current_turn += 1

    def get_transcript_summary(self, last_n: int = 6) -> str:
        """返回最近 N 轮发言的文本摘要（传给 LLM 做上下文）。"""
        recent = self.transcript[-last_n:]
        if not recent:
            return ""
        lines = []
        for msg in recent:
            lines.append("%s: %s" % (msg["speaker"], msg["text"]))
        return "\n".join(lines)

    def next_speaker_index(self) -> int:
        """轮询：下一个发言人的索引。"""
        return self.current_turn % len(self.participant_ids)

    def next_speaker_id(self) -> str:
        idx = self.next_speaker_index()
        return self.participant_ids[idx]

    def is_lead(self, worker_id: str) -> bool:
        """是否是主管（有权提议结束会议）。"""
        return worker_id == self.lead_worker_id

    def _bump_turn(self) -> None:
        """跳过一轮（LLM 空回复时调用），防止死循环。"""
        self.current_turn += 1
