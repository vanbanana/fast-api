class_name A2AController
extends Node

## A2A 对话控制器 — 集中管理 agent-to-agent 对话生命周期。
## 参考 Google A2A Task Lifecycle + AutoGen Two-Agent Chat。

signal a2a_event_requested(event_data: Dictionary)

const DISPLAY_SECONDS: float = 4.0
const TIMEOUT_SECONDS: float = 60.0

# 活跃对话 session：key = "speaker_id:listener_id"
var _sessions: Dictionary = {}
# 轮次计时器：key = "session_id:worker_id"
var _turn_timers: Dictionary = {}
# 超时计时器：key = session_id
var _timeout_timers: Dictionary = {}
# Worker 节点引用（由 office_demo 设置）
var worker_nodes: Dictionary = {}
# SpeechBubbles 引用
var speech_bubbles: Node = null
# PhoneUI 引用
var phone_ui: Node = null
# DebugPanel 引用
var debug_panel: Node = null


func _ready() -> void:
	pass


## 启动一次 A2A 对话（由 chat_started 信号触发）。
func start_chat(speaker: Node2D, listener: Node2D) -> void:
	var speaker_id := str(speaker.name)
	var listener_id := str(listener.name)

	# 并发保护：任一方已在对话中
	if is_worker_in_chat(speaker_id) or is_worker_in_chat(listener_id):
		if debug_panel != null:
			debug_panel.log_event("[A2A] 并发冲突: %s/%s 已在对话中" % [speaker_id, listener_id])
		return

	# 从发起人获取老板指令
	var directive_text := ""
	if speaker.has_method(&"get_debug_state"):
		var state = speaker.get_debug_state()
		directive_text = str(state.get("errand_directive_text", ""))

	# 创建本地 session 记录
	var session_key := "%s:%s" % [speaker_id, listener_id]
	_sessions[session_key] = {
		"speaker_id": speaker_id,
		"listener_id": listener_id,
		"directive_text": directive_text,
		"active": true,
	}

	if debug_panel != null:
		debug_panel.log_event("[CHAT] %s ↔ %s 开始交谈" % [speaker_id, listener_id])

	# 启动超时计时器
	_start_timeout_timer(session_key)

	# 发送 chat_started 事件给后端
	emit_signal("a2a_event_requested", {
		"type": "a2a_event",
		"worker_id": speaker_id,
		"payload": {
			"event": "chat_started",
			"session_id": "",
			"speaker_id": speaker_id,
			"listener_id": listener_id,
			"directive_text": directive_text,
		},
	})


## 处理后端发来的 chat_line 命令。
func apply_chat_line(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	var say_text := str(command.get("say", ""))
	var payload_value: Variant = command.get("payload", {})
	var display_seconds := DISPLAY_SECONDS
	var speaker_id := ""
	var listener_id := ""
	var session_id := ""

	if payload_value is Dictionary:
		var p := payload_value as Dictionary
		display_seconds = float(p.get("display_seconds", DISPLAY_SECONDS))
		speaker_id = str(p.get("speaker_id", ""))
		listener_id = str(p.get("listener_id", ""))
		session_id = str(p.get("session_id", ""))

	if worker_id.is_empty() or say_text.is_empty():
		return

	var worker_node = worker_nodes.get(worker_id) as Node
	if worker_node == null:
		return

	# 显示气泡
	if speech_bubbles != null:
		speech_bubbles.show_speech(worker_id, say_text)
	if phone_ui != null:
		phone_ui.add_chat_message(worker_id, say_text, false)

	# 让说话者面向对方
	var listener_node = worker_nodes.get(listener_id) as Node2D
	if listener_node != null and worker_node.has_method(&"face_toward"):
		(worker_node as Node2D).face_toward(listener_node.global_position)

	if debug_panel != null:
		debug_panel.log_event("[SAY] %s: %s" % [worker_id, say_text])

	# 刷新超时计时器
	var session_key := _find_session_key(speaker_id, listener_id)
	if not session_key.is_empty():
		_restart_timeout_timer(session_key)

	# 启动轮次计时器，显示完毕后自动请求下一轮
	_schedule_turn_timer(session_id, worker_id, speaker_id, listener_id, say_text, display_seconds)


## 处理后端发来的 chat_end 命令。
func apply_chat_end(command: Dictionary) -> void:
	var payload_value: Variant = command.get("payload", {})
	var speaker_id := ""
	var listener_id := ""
	var reason := "completed"

	if payload_value is Dictionary:
		var p := payload_value as Dictionary
		speaker_id = str(p.get("speaker_id", ""))
		listener_id = str(p.get("listener_id", ""))
		reason = str(p.get("reason", "completed"))

	if debug_panel != null:
		debug_panel.log_event("[CHAT_END] %s ↔ %s (%s)" % [speaker_id, listener_id, reason])

	var session_key := _find_session_key(speaker_id, listener_id)

	# 清理所有计时器
	_cleanup_session_timers(session_key)

	# 标记 session 结束
	if not session_key.is_empty() and _sessions.has(session_key):
		_sessions[session_key]["active"] = false
		_sessions.erase(session_key)

	# 结束交谈状态
	# 发起者(speaker)走过去找人的，需要回工位
	# 被找者(listener)从没离开工位，不需要回工位，直接恢复工作
	var speaker = worker_nodes.get(speaker_id) as Node
	var listener = worker_nodes.get(listener_id) as Node

	# 发起者：结束聊天 + 关外部决策 + 回工位
	if speaker != null:
		if speaker.has_method(&"cancel_seeking"):
			speaker.cancel_seeking()
		if speaker.has_method(&"set_external_decision_enabled"):
			speaker.set_external_decision_enabled(false)
		if speaker.has_method(&"return_to_seat"):
			speaker.return_to_seat()

	# 被找者：结束聊天 + 关外部决策，但不回工位（从没离开过）
	# 直接设 external_decision_enabled=false 但不调 force_idle
	# 让状态机从 end_chatting 恢复到之前的状态继续工作
	if listener != null:
		if listener.has_method(&"cancel_seeking"):
			listener.cancel_seeking()
		# 直接关外部决策，不触发 force_idle
		if "external_decision_enabled" in listener:
			listener.set("external_decision_enabled", false)


## 处理 chat_canceled 命令（老板新指令中断对话）。
func apply_chat_canceled(command: Dictionary) -> void:
	apply_chat_end(command)


## 检查某 worker 是否正在对话中。
func is_worker_in_chat(worker_id: String) -> bool:
	for key in _sessions:
		var session = _sessions[key] as Dictionary
		if session.get("active", false):
			if session.get("speaker_id", "") == worker_id or session.get("listener_id", "") == worker_id:
				return true
	return false


## 取消某 worker 参与的所有对话（老板新指令时调用）。
func cancel_chat_for_worker(worker_id: String) -> void:
	var to_remove := []
	for key in _sessions:
		var session = _sessions[key] as Dictionary
		if session.get("active", false):
			if session.get("speaker_id", "") == worker_id or session.get("listener_id", "") == worker_id:
				to_remove.append(key)
	for key in to_remove:
		var session = _sessions[key] as Dictionary
		apply_chat_end({
			"payload": {
				"speaker_id": session.get("speaker_id", ""),
				"listener_id": session.get("listener_id", ""),
				"reason": "canceled",
			}
		})


func cancel_all_chats() -> void:
	var to_remove := []
	for key in _sessions:
		var session = _sessions[key] as Dictionary
		if session.get("active", false):
			to_remove.append(key)
	for key in to_remove:
		var session = _sessions[key] as Dictionary
		apply_chat_end({
			"payload": {
				"speaker_id": session.get("speaker_id", ""),
				"listener_id": session.get("listener_id", ""),
				"reason": "canceled",
			}
		})


# ===== 内部方法 =====

func _find_session_key(speaker_id: String, listener_id: String) -> String:
	for key in _sessions:
		var session = _sessions[key] as Dictionary
		var sid1 := str(session.get("speaker_id", ""))
		var lid1 := str(session.get("listener_id", ""))
		if (sid1 == speaker_id and lid1 == listener_id) or (sid1 == listener_id and lid1 == speaker_id):
			return key
	return ""


func _get_worker_name(worker_id: String) -> String:
	var w = worker_nodes.get(worker_id) as Node
	if w != null and w.has_method(&"get_debug_state"):
		var state = w.get_debug_state()
		return str(state.get("name", worker_id))
	return worker_id


func _schedule_turn_timer(session_id: String, worker_id: String, speaker_id: String, listener_id: String, last_say: String, seconds: float) -> void:
	var key := "%s:%s" % [session_id, worker_id]
	# 清除旧计时器
	if _turn_timers.has(key):
		var old_timer := _turn_timers[key] as Timer
		if old_timer != null:
			old_timer.queue_free()

	var timer := Timer.new()
	timer.one_shot = true
	timer.wait_time = max(seconds, 2.0)
	timer.timeout.connect(_on_turn_timeout.bind(worker_id, speaker_id, listener_id, last_say))
	add_child(timer)
	timer.start()
	_turn_timers[key] = timer


func _on_turn_timeout(worker_id: String, speaker_id: String, listener_id: String, last_say: String) -> void:
	# 检查 session 是否还活跃
	var session_key := _find_session_key(speaker_id, listener_id)
	if session_key.is_empty():
		return
	var session = _sessions.get(session_key) as Dictionary
	if session == null or not session.get("active", false):
		return

	# 发送 chat_turn 事件给后端
	emit_signal("a2a_event_requested", {
		"type": "a2a_event",
		"worker_id": worker_id,
		"payload": {
			"event": "chat_turn",
			"session_id": "",
			"speaker_id": speaker_id,
			"listener_id": listener_id,
			"last_sayer_id": worker_id,
			"last_text": last_say,
		},
	})


func _start_timeout_timer(session_key: String) -> void:
	_stop_timeout_timer(session_key)
	var timer := Timer.new()
	timer.one_shot = true
	timer.wait_time = TIMEOUT_SECONDS
	timer.timeout.connect(_on_session_timeout.bind(session_key))
	add_child(timer)
	timer.start()
	_timeout_timers[session_key] = timer


func _restart_timeout_timer(session_key: String) -> void:
	_start_timeout_timer(session_key)


func _stop_timeout_timer(session_key: String) -> void:
	if _timeout_timers.has(session_key):
		var timer := _timeout_timers[session_key] as Timer
		if timer != null:
			timer.queue_free()
		_timeout_timers.erase(session_key)


func _on_session_timeout(session_key: String) -> void:
	var session = _sessions.get(session_key) as Dictionary
	if session == null or not session.get("active", false):
		return

	var speaker_id := str(session.get("speaker_id", ""))
	var listener_id := str(session.get("listener_id", ""))

	if debug_panel != null:
		debug_panel.log_event("[A2A] 对话超时: %s ↔ %s" % [speaker_id, listener_id])

	# 发送 chat_timeout 给后端
	emit_signal("a2a_event_requested", {
		"type": "a2a_event",
		"worker_id": speaker_id,
		"payload": {
			"event": "chat_timeout",
			"speaker_id": speaker_id,
			"listener_id": listener_id,
		},
	})

	# 本地清理
	_cleanup_session_timers(session_key)
	_sessions.erase(session_key)

	for wid in [speaker_id, listener_id]:
		var w = worker_nodes.get(wid) as Node
		if w != null:
			if w.has_method(&"cancel_seeking"):
				w.cancel_seeking()
			if w.has_method(&"return_to_seat"):
				w.return_to_seat()


func _cleanup_session_timers(session_key: String) -> void:
	# 清理超时计时器
	_stop_timeout_timer(session_key)

	# 清理轮次计时器
	var to_remove := []
	for key in _turn_timers:
		var timer := _turn_timers[key] as Timer
		if timer != null:
			timer.queue_free()
		to_remove.append(key)
	for key in to_remove:
		_turn_timers.erase(key)
