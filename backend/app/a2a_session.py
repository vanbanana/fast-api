"""A2A 对话协议核心 — 参考 Google A2A Task Lifecycle + AutoGen Two-Agent Chat。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class SessionState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMEOUT = "timeout"


# 配置常量
MAX_TURNS = 8
MAX_LLM_FAILURES = 3
SEMANTIC_LOOP_WINDOW = 3
SEMANTIC_LOOP_THRESHOLD = 0.7
SESSION_TIMEOUT_SECONDS = 60.0


@dataclass
class A2AMessage:
    """A2A 标准消息格式。"""
    session_id: str
    message_id: int
    role: str  # "initiator" | "responder"
    worker_id: str
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class A2ASession:
    """A2A 对话会话 — 完整生命周期状态机。"""
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    initiator_id: str = ""
    responder_id: str = ""
    directive_text: str = ""
    state: SessionState = SessionState.PENDING
    transcript: list[A2AMessage] = field(default_factory=list)
    llm_fail_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    _interrupted_status_before_chat: str | None = None
    assigned_task_title: str = ""  # 对话中通过 function calling 分配的任务标题

    # --- 生命周期转换 ---

    def activate(self) -> None:
        """pending → active"""
        if self.state == SessionState.PENDING:
            self.state = SessionState.ACTIVE
            self._touch()

    def complete(self) -> None:
        """active → completed"""
        if self.state == SessionState.ACTIVE:
            self.state = SessionState.COMPLETED
            self._touch()

    def fail(self) -> None:
        """active → failed"""
        if self.state == SessionState.ACTIVE:
            self.state = SessionState.FAILED
            self._touch()

    def cancel(self) -> None:
        """active/pending → canceled"""
        if self.state in (SessionState.ACTIVE, SessionState.PENDING):
            self.state = SessionState.CANCELED
            self._touch()

    def timeout(self) -> None:
        """active → timeout"""
        if self.state == SessionState.ACTIVE:
            self.state = SessionState.TIMEOUT
            self._touch()

    # --- 轮次管理 ---

    @property
    def turn_count(self) -> int:
        return len(self.transcript)

    @property
    def current_speaker_id(self) -> str:
        """当前该谁说话：偶数轮=initiator，奇数轮=responder"""
        return self.initiator_id if self.turn_count % 2 == 0 else self.responder_id

    @property
    def current_listener_id(self) -> str:
        return self.responder_id if self.turn_count % 2 == 0 else self.initiator_id

    @property
    def current_role(self) -> str:
        return "initiator" if self.turn_count % 2 == 0 else "responder"

    @property
    def is_terminal(self) -> bool:
        return self.state in (SessionState.COMPLETED, SessionState.FAILED, SessionState.CANCELED, SessionState.TIMEOUT)

    @property
    def is_active(self) -> bool:
        return self.state == SessionState.ACTIVE

    def record_line(self, worker_id: str, content: str) -> A2AMessage:
        """记录一轮对话，返回 A2AMessage。"""
        msg = A2AMessage(
            session_id=self.session_id,
            message_id=self.turn_count,
            role=self.current_role,
            worker_id=worker_id,
            content=content,
        )
        self.transcript.append(msg)
        self._touch()
        return msg

    def get_transcript_summary(self, last_n: int = 4) -> str:
        """返回最近 N 轮对话的文本摘要。"""
        recent = self.transcript[-last_n:]
        if not recent:
            return ""
        lines = []
        for msg in recent:
            lines.append("%s: %s" % (msg.worker_id, msg.content))
        return "\n".join(lines)

    # --- 智能终止条件 ---

    def should_terminate(self, llm_done: bool = False) -> bool:
        """检查是否应该终止对话。"""
        if self.is_terminal:
            return True

        # 1. LLM 明确表示对话结束（最可靠）
        if llm_done:
            return True

        # 2. max_turns 硬上限
        if self.turn_count >= MAX_TURNS:
            return True

        # 3. 语义循环检测（内容高度重复）
        if self._detect_semantic_loop():
            return True

        return False

    def _detect_semantic_loop(self) -> bool:
        """Jaccard 语义循环检测：最近 3 轮内容相似度 > 0.7。"""
        if len(self.transcript) < SEMANTIC_LOOP_WINDOW:
            return False

        recent = self.transcript[-SEMANTIC_LOOP_WINDOW:]
        # 计算相邻轮次的 Jaccard 相似度
        similarities = []
        for i in range(len(recent) - 1):
            sim = self._jaccard(recent[i].content, recent[i + 1].content)
            similarities.append(sim)

        # 所有相邻对都高度相似 → 循环
        return all(s > SEMANTIC_LOOP_THRESHOLD for s in similarities)

    @staticmethod
    def _jaccard(a: str, b: str) -> float:
        """简单的字符级 Jaccard 相似度。"""
        set_a = set(a)
        set_b = set(b)
        if not set_a and not set_b:
            return 1.0
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    # --- 错误恢复 ---

    def record_llm_failure(self) -> bool:
        """记录一次 LLM 失败，返回是否应该终止。"""
        self.llm_fail_count += 1
        if self.llm_fail_count >= MAX_LLM_FAILURES:
            self.fail()
            return True
        return False

    def record_llm_success(self) -> None:
        """LLM 成功时重置失败计数。"""
        self.llm_fail_count = 0

    # --- 超时检测 ---

    def is_timed_out(self) -> bool:
        """检查是否超时（60 秒无活动）。"""
        if self.is_terminal:
            return False
        return (time.time() - self.last_activity_at) > SESSION_TIMEOUT_SECONDS

    # --- 并发控制辅助 ---

    def participant_ids(self) -> set[str]:
        """返回参与此对话的所有 worker ID。"""
        return {self.initiator_id, self.responder_id}

    # --- 内部 ---

    def _touch(self) -> None:
        self.last_activity_at = time.time()


class A2ASessionManager:
    """A2A 对话会话管理器 — 并发控制 + 双向索引。"""

    def __init__(self) -> None:
        self._sessions: dict[str, A2ASession] = {}  # session_id → session
        self._worker_index: dict[str, str] = {}  # worker_id → session_id

    def create_session(self, initiator_id: str, responder_id: str, directive_text: str = "") -> A2ASession | None:
        """创建新对话 session，如果任一方已在对话中则返回 None。"""
        if self.is_worker_busy(initiator_id) or self.is_worker_busy(responder_id):
            return None

        session = A2ASession(
            initiator_id=initiator_id,
            responder_id=responder_id,
            directive_text=directive_text,
        )
        self._sessions[session.session_id] = session
        self._worker_index[initiator_id] = session.session_id
        self._worker_index[responder_id] = session.session_id
        return session

    def get_session(self, session_id: str) -> A2ASession | None:
        return self._sessions.get(session_id)

    def get_session_by_workers(self, worker_a: str, worker_b: str) -> A2ASession | None:
        """通过两个 worker ID 查找活跃 session。"""
        sid = self._worker_index.get(worker_a)
        if sid is None:
            return None
        session = self._sessions.get(sid)
        if session is None or session.is_terminal:
            return None
        if worker_b in session.participant_ids():
            return session
        return None

    def is_worker_busy(self, worker_id: str) -> bool:
        """检查 worker 是否正在对话中。"""
        sid = self._worker_index.get(worker_id)
        if sid is None:
            return False
        session = self._sessions.get(sid)
        return session is not None and not session.is_terminal

    def remove_session(self, session_id: str) -> None:
        """清理已终止的 session。"""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            for wid in session.participant_ids():
                self._worker_index.pop(wid, None)

    def cleanup_expired(self) -> list[str]:
        """清理超时的 session，返回被清理的 session_id 列表。"""
        expired = []
        for sid, session in list(self._sessions.items()):
            if session.is_timed_out():
                session.timeout()
                expired.append(sid)
                self.remove_session(sid)
        return expired

    def cancel_sessions_for_worker(self, worker_id: str) -> A2ASession | None:
        """取消某 worker 参与的所有活跃 session，返回被取消的 session。"""
        sid = self._worker_index.get(worker_id)
        if sid is None:
            return None
        session = self._sessions.get(sid)
        if session is not None and not session.is_terminal:
            session.cancel()
            self.remove_session(sid)
            return session
        return None
