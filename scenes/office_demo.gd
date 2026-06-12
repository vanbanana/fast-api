extends Node2D
## Demo 入口编排器：场景目标同步、后端命令分发、组件装配。
## WebSocket / 气泡 / 详情面板 / debug 面板 / 老板 UI 拆分在 scenes/components/ 下。

const DEFAULT_PIXEL_UI_THEME := preload("res://ui/pixel_ui_theme.gd")
const BackendClient := preload("res://scenes/components/backend_client.gd")
const SpeechBubbles := preload("res://scenes/components/speech_bubbles.gd")
const DetailPanel := preload("res://scenes/components/detail_panel.gd")
const DebugPanel := preload("res://scenes/components/debug_panel.gd")
const BossCommandUi := preload("res://scenes/components/boss_command_ui.gd")
const AgentStateStore := preload("res://scenes/components/agent_state_store.gd")
const TokenBar := preload("res://scenes/components/token_bar.gd")

@export_category("后端连接")
@export var backend_enabled: bool = true
@export var backend_url: String = "ws://127.0.0.1:8000/ws/office"
@export var reconnect_seconds: float = 3.0
@export var backend_agent_prompts_dir: String = "backend/prompts/agents"
@export var boss_ui_enabled: bool = true
@export var hover_detail_enabled: bool = true
@export var speech_bubble_enabled: bool = true
@export var debug_ui_enabled: bool = true
@export var worker_hover_radius: float = 14.0

@export_category("程序化像素 UI")
@export var pixel_ui_theme: PixelUiTheme

@export_category("场景目标分组")
@export var worker_group: StringName = &"demo_workers"
@export var seat_marker_group: StringName = &"seat_markers"
@export var idle_marker_group: StringName = &"idle_markers"
@export var roam_marker_group: StringName = &"supMarkers"

var backend: BackendClient
var speech_bubbles: SpeechBubbles
var detail_panel: DetailPanel
var debug_panel: DebugPanel
var boss_ui: BossCommandUi
var token_bar: TokenBar
var agent_store := AgentStateStore.new()

var worker_nodes: Dictionary = {}
var pending_say_by_worker: Dictionary = {}
var meeting_say_done_timers: Dictionary = {}
var local_worker_profiles: Dictionary = {
	"worker1": {"name": "林主管", "role": "项目经理"},
	"worker2": {"name": "小周", "role": "后端工程师"},
	"worker3": {"name": "阿晴", "role": "产品经理"},
	"worker4": {"name": "老陈", "role": "架构师"},
	"worker5": {"name": "米娅", "role": "UI 设计师"},
	"worker6": {"name": "小赵", "role": "测试工程师"},
	"worker7": {"name": "Niko", "role": "运营"},
	"worker8": {"name": "安娜", "role": "数据分析师"},
	"worker9": {"name": "小吴", "role": "前端工程师"},
	"worker10": {"name": "Rin", "role": "实习生"},
	"worker11": {"name": "乔伊", "role": "HR"},
}


func _ready() -> void:
	if pixel_ui_theme == null:
		pixel_ui_theme = DEFAULT_PIXEL_UI_THEME.new()

	# demo 入口不直接控制角色，只统一接入角色信号。
	for worker in get_tree().get_nodes_in_group(worker_group):
		worker_nodes[str(worker.name)] = worker
		if worker.has_signal(&"target_reached"):
			worker.target_reached.connect(_on_worker_target_reached.bind(worker))
		if worker.has_signal(&"decision_requested"):
			worker.decision_requested.connect(_on_worker_decision_requested.bind(worker))

	if boss_ui_enabled:
		boss_ui = BossCommandUi.new()
		boss_ui.name = "BossCommandUi"
		add_child(boss_ui)
		boss_ui.setup(pixel_ui_theme)
		boss_ui.command_submitted.connect(send_boss_command)

	if hover_detail_enabled:
		detail_panel = DetailPanel.new()
		detail_panel.name = "DetailPanel"
		detail_panel.worker_group = worker_group
		detail_panel.worker_hover_radius = worker_hover_radius
		detail_panel.backend_agent_prompts_dir = backend_agent_prompts_dir
		detail_panel.local_worker_profiles = local_worker_profiles
		add_child(detail_panel)
		detail_panel.setup(self, pixel_ui_theme, agent_store)

	token_bar = TokenBar.new()
	token_bar.name = "TokenBar"
	add_child(token_bar)
	token_bar.setup(pixel_ui_theme)

	if speech_bubble_enabled:
		speech_bubbles = SpeechBubbles.new()
		speech_bubbles.name = "SpeechBubbles"
		add_child(speech_bubbles)
		speech_bubbles.setup(pixel_ui_theme, worker_nodes)

	if debug_ui_enabled:
		debug_panel = DebugPanel.new()
		debug_panel.name = "DebugPanel"
		add_child(debug_panel)
		debug_panel.setup(pixel_ui_theme, agent_store, _debug_status_line, _debug_worker_lines)

	if backend_enabled:
		backend = BackendClient.new()
		backend.name = "BackendClient"
		backend.url = backend_url
		backend.reconnect_seconds = reconnect_seconds
		backend.connected.connect(_on_backend_connected)
		backend.disconnected.connect(_on_backend_disconnected)
		backend.command_received.connect(_apply_agent_command)
		backend.parse_failed.connect(_on_backend_parse_failed)
		backend.message_sent.connect(_on_backend_message_sent)
		backend.send_blocked.connect(_on_backend_message_sent)
		add_child(backend)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey:
		var key_event := event as InputEventKey
		if key_event.pressed and key_event.keycode == KEY_F12:
			if debug_panel != null:
				debug_panel.toggle()
			return

	if detail_panel == null:
		return
	if !(event is InputEventMouseButton):
		return

	var mouse_event := event as InputEventMouseButton
	if mouse_event.button_index == MOUSE_BUTTON_LEFT and mouse_event.pressed:
		detail_panel.handle_click()


func _backend_connected() -> bool:
	return backend != null and backend.is_connected_to_backend()


func _on_backend_connected() -> void:
	_set_workers_external_decision(true)
	_send_world_snapshot()
	_set_status("后端已连接")


func _on_backend_disconnected() -> void:
	_set_workers_external_decision(false)
	_set_status("后端断开，角色使用本地逻辑")


func _on_backend_parse_failed(text: String) -> void:
	if debug_panel != null:
		debug_panel.log_received("PARSE_FAILED %s" % text)


func _on_backend_message_sent(summary: String) -> void:
	if debug_panel != null:
		debug_panel.log_sent(summary)


func _set_workers_external_decision(enabled: bool) -> void:
	for worker in get_tree().get_nodes_in_group(worker_group):
		if worker.has_method(&"set_external_decision_enabled"):
			worker.set_external_decision_enabled(enabled)


func _send_world_snapshot() -> void:
	# 后端只知道这里同步过去的 Marker；目标名称、分组和位置都由 Godot 场景管理。
	var targets: Array[Dictionary] = []
	var seat_targets := _collect_targets(seat_marker_group)
	var idle_targets := _collect_targets(idle_marker_group)
	var roam_targets := _collect_targets(roam_marker_group)
	targets.append_array(seat_targets)
	targets.append_array(idle_targets)
	targets.append_array(roam_targets)
	if debug_panel != null:
		debug_panel.target_counts = {
			"seat": seat_targets.size(),
			"idle": idle_targets.size(),
			"roam": roam_targets.size(),
			"total": targets.size(),
		}
	backend.send_json({
		"type": "world_snapshot",
		"worker_id": "office",
		"payload": {
			"targets": targets,
		},
	})


func _collect_targets(group_name: StringName) -> Array[Dictionary]:
	var targets: Array[Dictionary] = []
	for node in get_tree().get_nodes_in_group(group_name):
		if node is Marker2D:
			targets.append({
				"id": str(node.name),
				"group": str(group_name),
			})
	return targets


func _on_worker_target_reached(target: Marker2D, worker: Node) -> void:
	var worker_id := str(worker.name)
	if pending_say_by_worker.has(worker_id):
		_show_speech(worker_id, str(pending_say_by_worker[worker_id]))
		pending_say_by_worker.erase(worker_id)

	if !_backend_connected():
		return

	backend.send_json({
		"type": "worker_arrived",
		"worker_id": worker_id,
		"target_id": str(target.name),
		"target_group": _get_target_group(target),
	})


func _on_worker_decision_requested(worker: Node) -> void:
	if !_backend_connected():
		if worker.has_method(&"wait_for_next_decision"):
			worker.wait_for_next_decision()
		return

	backend.send_json({
		"type": "worker_ready",
		"worker_id": str(worker.name),
		"payload": {
			"reason": "wait_timer_timeout",
		},
	})


func _get_target_group(target: Marker2D) -> String:
	if target.is_in_group(seat_marker_group):
		return str(seat_marker_group)
	if target.is_in_group(idle_marker_group):
		return str(idle_marker_group)
	if target.is_in_group(roam_marker_group):
		return str(roam_marker_group)
	return ""


func send_boss_command(text: String, target_worker_ids: Array[String] = [], priority: int = 2) -> void:
	# 老板指令会交给后端影响对应员工。
	if backend == null:
		_set_status("后端未启用，无法发送老板指令")
		return
	_clear_all_speech_bubbles()
	if debug_panel != null:
		debug_panel.log_sent("BOSS_INPUT %s" % text)
	backend.send_json({
		"type": "boss_command",
		"worker_id": "boss",
		"payload": {
			"text": text,
			"target_worker_ids": target_worker_ids,
			"priority": priority,
		},
	})
	_set_status("已发送老板指令")


func _apply_agent_command(command: Dictionary) -> void:
	if debug_panel != null:
		debug_panel.log_received_command(command)
	var action := str(command.get("action", ""))
	if action == "token_usage":
		var usage_value: Variant = command.get("payload", {})
		if token_bar != null and usage_value is Dictionary:
			token_bar.update_usage(usage_value as Dictionary)
		return

	agent_store.cache_command(command)
	_update_status_bubble(str(command.get("worker_id", "")))

	if action == "say":
		_apply_say_command(command)
		return
	if action == "idle":
		_apply_idle_command(command)
		return
	if action != "move_to":
		return

	var worker_id := str(command.get("worker_id", ""))
	var target_id := StringName(str(command.get("target_id", "")))
	if worker_id.is_empty() or str(target_id).is_empty():
		return

	var workers := get_node_or_null(^"workers")
	if workers == null:
		return

	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker == null:
		return

	var travel_mode := "normal"
	var payload_value: Variant = command.get("payload", {})
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		travel_mode = str(payload_dict.get("travel_mode", "normal"))
	var moved: bool = false
	if travel_mode == "meeting" and worker.has_method(&"force_seat_marker_id"):
		moved = worker.force_seat_marker_id(target_id)
	elif travel_mode == "visit" and worker.has_method(&"visit_marker_id"):
		moved = worker.visit_marker_id(target_id)
	elif worker.has_method(&"move_to_marker_id"):
		moved = worker.move_to_marker_id(target_id)
	var say := str(command.get("say", ""))
	if !moved:
		_set_status("%s 无法到达 %s" % [worker_id, str(target_id)])
		if debug_panel != null:
			debug_panel.log_received("MOVE_FAILED %s -> %s mode=%s" % [worker_id, str(target_id), travel_mode])
		if worker.has_method(&"wait_for_next_decision"):
			worker.wait_for_next_decision()
		return
	if !say.is_empty():
		pending_say_by_worker[worker_id] = say


func _apply_idle_command(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	if worker_id.is_empty() or worker_id == "office":
		return

	var payload_value: Variant = command.get("payload", {})
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		if str(payload_dict.get("travel_mode", "")) == "meeting":
			return

	var workers := get_node_or_null(^"workers")
	if workers == null:
		return

	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker != null and worker.has_method(&"wait_for_next_decision"):
		worker.wait_for_next_decision()


func _apply_say_command(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	var say := str(command.get("say", ""))
	if worker_id.is_empty() or say.is_empty():
		return

	var payload_value: Variant = command.get("payload", {})
	if !(payload_value is Dictionary):
		return

	var payload := payload_value as Dictionary
	if str(payload.get("display", "")) != "speech":
		return

	_show_speech(worker_id, say)
	var session_id := str(payload.get("meeting_session_id", ""))
	if !session_id.is_empty():
		_schedule_meeting_say_done(worker_id, session_id, maxf(1.2, float(say.length()) / 8.0))


func _schedule_meeting_say_done(worker_id: String, session_id: String, seconds: float) -> void:
	var key := "%s:%s" % [session_id, worker_id]
	if meeting_say_done_timers.has(key):
		var old_timer := meeting_say_done_timers[key] as Timer
		if old_timer != null:
			old_timer.queue_free()

	var timer := Timer.new()
	timer.one_shot = true
	timer.wait_time = seconds
	timer.timeout.connect(_on_meeting_say_done_timeout.bind(worker_id, session_id, key))
	add_child(timer)
	meeting_say_done_timers[key] = timer
	timer.start()


func _on_meeting_say_done_timeout(worker_id: String, session_id: String, key: String) -> void:
	meeting_say_done_timers.erase(key)
	if !_backend_connected():
		return
	backend.send_json({
		"type": "meeting_say_done",
		"worker_id": worker_id,
		"payload": {
			"session_id": session_id,
		},
	})


func _show_speech(worker_id: String, text: String) -> void:
	if speech_bubbles != null:
		speech_bubbles.show_speech(worker_id, text)


func _update_status_bubble(worker_id: String) -> void:
	if speech_bubbles == null or worker_id.is_empty() or worker_id == "office":
		return
	var snapshot := agent_store.snapshot_for(worker_id)
	speech_bubbles.set_status(worker_id, str(snapshot.get("status", "")))


func _clear_all_speech_bubbles() -> void:
	pending_say_by_worker.clear()
	if speech_bubbles != null:
		speech_bubbles.clear_all()


func _set_status(text: String) -> void:
	if boss_ui != null:
		boss_ui.set_status(text)


func _debug_status_line() -> String:
	var socket_state := "DISABLED"
	if backend != null:
		socket_state = backend.socket_state_name()
	var status := ""
	if boss_ui != null:
		status = boss_ui.status_text()
	return "backend_connected=%s  socket=%s  url=%s\nstatus=%s" % [
		str(_backend_connected()), socket_state, backend_url, status,
	]


func _debug_worker_lines() -> Array[String]:
	var lines: Array[String] = []
	var worker_ids := worker_nodes.keys()
	worker_ids.sort()
	for worker_id in worker_ids:
		lines.append(_debug_worker_line(str(worker_id)))
	return lines


func _debug_worker_line(worker_id: String) -> String:
	var worker := worker_nodes.get(worker_id) as Node2D
	var position_text := "?"
	if worker != null:
		position_text = "(%.1f, %.1f)" % [worker.global_position.x, worker.global_position.y]

	var move_text := "target=? seat=? override=? stuck=? anim=?"
	if worker != null and worker.has_method(&"get_debug_state"):
		var state_value: Variant = worker.get_debug_state()
		if state_value is Dictionary:
			var state := state_value as Dictionary
			move_text = "target=%s seat=%s override=%s stuck=%.2f anim=%s nav=%s reserved=%s" % [
				str(state.get("target", "")),
				str(state.get("is_seat", false)),
				str(state.get("override", false)),
				float(state.get("stuck_time", 0.0)),
				str(state.get("animation", "")),
				str(state.get("navigation_finished", "")),
				str(state.get("reserved", "")),
			]

	var snapshot := agent_store.snapshot_for(worker_id)
	var context := agent_store.context_for(worker_id)
	var bubble_mode := "hidden"
	if speech_bubbles != null:
		bubble_mode = speech_bubbles.mode_for(worker_id)
	var pending := str(pending_say_by_worker.has(worker_id))
	return "%s pos=%s %s status=%s backend_target=%s travel=%s bubble=%s pending=%s" % [
		worker_id,
		position_text,
		move_text,
		str(snapshot.get("status", "")),
		str(context.get("target_id", snapshot.get("last_target_id", ""))),
		str(snapshot.get("travel_mode", "")),
		bubble_mode,
		pending,
	]
