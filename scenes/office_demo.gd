extends Node2D

const DEFAULT_PIXEL_UI_THEME := preload("res://ui/pixel_ui_theme.gd")

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

var socket := WebSocketPeer.new()
var backend_connected: bool = false
var reconnect_timer: float = 0.0
var command_input: LineEdit
var status_label: Label
var debug_panel: PanelContainer
var debug_scroll: ScrollContainer
var debug_label: Label
var debug_visible: bool = false
var debug_sent_log: Array[String] = []
var debug_received_log: Array[String] = []
var debug_target_counts: Dictionary = {}
var detail_panel: PanelContainer
var detail_label: Label
var detail_tab_buttons: Dictionary = {}
var active_detail_tab: String = "profile"
var agent_snapshots: Dictionary = {}
var agent_work_contexts: Dictionary = {}
var agent_streams: Dictionary = {}
var worker_nodes: Dictionary = {}
var speech_panels: Dictionary = {}
var speech_labels: Dictionary = {}
var speech_full_text: Dictionary = {}
var speech_visible_chars: Dictionary = {}
var speech_modes: Dictionary = {}
var speech_hold_timers: Dictionary = {}
var speech_dot_timers: Dictionary = {}
var pending_say_by_worker: Dictionary = {}
var meeting_say_done_timers: Dictionary = {}
var hovered_worker: Node2D
var focused_worker: Node2D
var pinned_worker: Node2D
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

	# demo 入口不直接控制角色，只统一接入角色信号，便于后续加调试 UI。
	for worker in get_tree().get_nodes_in_group(worker_group):
		worker_nodes[str(worker.name)] = worker
		if worker.has_signal(&"target_reached"):
			worker.target_reached.connect(_on_worker_target_reached.bind(worker))
		if worker.has_signal(&"decision_requested"):
			worker.decision_requested.connect(_on_worker_decision_requested.bind(worker))

	if backend_enabled:
		_connect_backend()
	if boss_ui_enabled:
		_create_boss_ui()
	if hover_detail_enabled:
		_create_hover_detail_ui()
	if speech_bubble_enabled:
		_create_speech_bubbles()
	if debug_ui_enabled:
		_create_debug_ui()


func _process(delta: float) -> void:
	if speech_bubble_enabled:
		_update_speech_bubbles(delta)
	if hover_detail_enabled:
		_update_hover_detail()
	if debug_ui_enabled and debug_visible:
		_update_debug_ui()

	if !backend_enabled:
		return

	var state := socket.get_ready_state()
	if state != WebSocketPeer.STATE_CLOSED:
		socket.poll()
		state = socket.get_ready_state()

	if state == WebSocketPeer.STATE_OPEN:
		if !backend_connected:
			backend_connected = true
			_set_workers_external_decision(true)
			_send_world_snapshot()
			_set_status("后端已连接")
		_read_backend_commands()
		return

	if backend_connected:
		backend_connected = false
		_set_workers_external_decision(false)
		_set_status("后端断开，角色使用本地逻辑")

	reconnect_timer += delta
	if reconnect_timer >= reconnect_seconds:
		_connect_backend()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey:
		var key_event := event as InputEventKey
		if key_event.pressed and key_event.keycode == KEY_F12:
			_toggle_debug_ui()
			return

	if !hover_detail_enabled:
		return
	if !(event is InputEventMouseButton):
		return

	var mouse_event := event as InputEventMouseButton
	if mouse_event.button_index != MOUSE_BUTTON_LEFT or !mouse_event.pressed:
		return
	if _is_mouse_over_detail_panel():
		return

	var clicked_worker := _find_hovered_worker()
	if clicked_worker == null:
		pinned_worker = null
		return
	if pinned_worker == clicked_worker:
		pinned_worker = null
	else:
		pinned_worker = clicked_worker
		focused_worker = clicked_worker


func _connect_backend() -> void:
	# WebSocketPeer 关闭后重新创建，避免复用旧连接状态。
	socket = WebSocketPeer.new()
	reconnect_timer = 0.0
	var error := socket.connect_to_url(backend_url)
	if error != OK:
		backend_connected = false
		_set_status("后端连接失败")


func _set_workers_external_decision(enabled: bool) -> void:
	for worker in get_tree().get_nodes_in_group(worker_group):
		if worker.has_method(&"set_external_decision_enabled"):
			worker.set_external_decision_enabled(enabled)


func _read_backend_commands() -> void:
	while socket.get_available_packet_count() > 0:
		var text := socket.get_packet().get_string_from_utf8()
		var data: Variant = JSON.parse_string(text)
		if data is Dictionary:
			_apply_agent_command(data)
		else:
			_push_debug_log(debug_received_log, "PARSE_FAILED %s" % text)


func _send_world_snapshot() -> void:
	# 后端只知道这里同步过去的 Marker；目标名称、分组和位置都由 Godot 场景管理。
	var targets: Array[Dictionary] = []
	var seat_targets := _collect_targets(seat_marker_group)
	var idle_targets := _collect_targets(idle_marker_group)
	var roam_targets := _collect_targets(roam_marker_group)
	targets.append_array(seat_targets)
	targets.append_array(idle_targets)
	targets.append_array(roam_targets)
	debug_target_counts = {
		"seat": seat_targets.size(),
		"idle": idle_targets.size(),
		"roam": roam_targets.size(),
		"total": targets.size(),
	}
	_send_json({
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

	if !backend_connected:
		return

	_send_json({
		"type": "worker_arrived",
		"worker_id": worker_id,
		"target_id": str(target.name),
		"target_group": _get_target_group(target),
	})


func _on_worker_decision_requested(worker: Node) -> void:
	if !backend_connected:
		if worker.has_method(&"wait_for_next_decision"):
			worker.wait_for_next_decision()
		return

	_send_json({
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


func _send_json(payload: Dictionary) -> void:
	if socket.get_ready_state() != WebSocketPeer.STATE_OPEN:
		_push_debug_log(debug_sent_log, "SEND_BLOCKED socket=%s %s" % [_socket_state_name(), _debug_payload_summary(payload)])
		return
	var text := JSON.stringify(payload)
	_push_debug_log(debug_sent_log, _debug_payload_summary(payload))
	socket.send_text(text)


func send_boss_command(text: String, target_worker_ids: Array[String] = [], priority: int = 2) -> void:
	# 后续接 UI 输入框时直接调用这个方法，老板指令会交给后端影响对应员工。
	_clear_all_speech_bubbles()
	_push_debug_log(debug_sent_log, "BOSS_INPUT %s" % text)
	_send_json({
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
	_push_debug_log(debug_received_log, _debug_command_summary(command))
	var action := str(command.get("action", ""))
	if action == "stream_delta":
		_cache_agent_output(command)
		return
	if action == "say":
		_cache_agent_output(command)
		_apply_say_command(command)
		return
	if action == "idle":
		_cache_agent_output(command)
		_apply_idle_command(command)
		return
	if action != "move_to":
		_cache_agent_output(command)
		return

	_cache_agent_output(command)
	var worker_id := str(command.get("worker_id", ""))
	var target_id := StringName(str(command.get("target_id", "")))
	if worker_id.is_empty() or str(target_id).is_empty():
		return

	var workers := get_node_or_null(^"workers")
	if workers == null:
		return

	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker != null:
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
			_push_debug_log(debug_received_log, "MOVE_FAILED %s -> %s mode=%s" % [worker_id, str(target_id), travel_mode])
			if worker.has_method(&"wait_for_next_decision"):
				worker.wait_for_next_decision()
			return
		if !say.is_empty():
			pending_say_by_worker[worker_id] = say


func _cache_agent_output(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	if worker_id.is_empty() or worker_id == "office":
		return

	var say := str(command.get("say", ""))
	if str(command.get("action", "")) == "stream_delta":
		if say.is_empty():
			return
		var stream_value: Variant = agent_streams.get(worker_id, [])
		var stream: Array = []
		if stream_value is Array:
			stream = stream_value as Array
		stream.append(say)
		if stream.size() > 24:
			stream = stream.slice(stream.size() - 24)
		agent_streams[worker_id] = stream
		return

	var payload: Variant = command.get("payload", {})
	if payload is Dictionary and !payload.is_empty():
		agent_snapshots[worker_id] = payload
		if payload.has("work_context") and payload["work_context"] is Dictionary:
			agent_work_contexts[worker_id] = payload["work_context"]

	if !say.is_empty():
		if !agent_snapshots.has(worker_id):
			agent_snapshots[worker_id] = {}
		var snapshot: Dictionary = agent_snapshots[worker_id] as Dictionary
		snapshot["last_say"] = say
		agent_snapshots[worker_id] = snapshot


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
	if !backend_connected:
		return
	_send_json({
		"type": "meeting_say_done",
		"worker_id": worker_id,
		"payload": {
			"session_id": session_id,
		},
	})


func _create_debug_ui() -> void:
	var layer := CanvasLayer.new()
	layer.name = "DebugLayer"
	layer.layer = 50
	add_child(layer)

	debug_panel = PanelContainer.new()
	debug_panel.name = "DebugPanel"
	debug_panel.visible = false
	debug_panel.position = Vector2(8, 92)
	debug_panel.custom_minimum_size = Vector2(620, 560)
	debug_panel.mouse_filter = Control.MOUSE_FILTER_PASS
	_apply_pixel_panel_style(debug_panel)
	layer.add_child(debug_panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", pixel_ui_theme.row_separation)
	debug_panel.add_child(box)

	var title := Label.new()
	title.text = "F12 Debug 链路面板"
	title.add_theme_font_size_override("font_size", pixel_ui_theme.ui_font_size)
	title.add_theme_color_override("font_color", pixel_ui_theme.detail_text_color)
	box.add_child(title)

	debug_scroll = ScrollContainer.new()
	debug_scroll.custom_minimum_size = Vector2(596, 500)
	debug_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(debug_scroll)

	debug_label = Label.new()
	debug_label.custom_minimum_size = Vector2(572, 0)
	debug_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	debug_label.add_theme_font_size_override("font_size", pixel_ui_theme.detail_font_size)
	debug_label.add_theme_color_override("font_color", pixel_ui_theme.detail_text_color)
	debug_scroll.add_child(debug_label)


func _toggle_debug_ui() -> void:
	debug_visible = !debug_visible
	if debug_panel != null:
		debug_panel.visible = debug_visible
	if debug_visible:
		_update_debug_ui()


func _update_debug_ui() -> void:
	if debug_label == null:
		return

	var lines: Array[String] = []
	lines.append("按 F12 关闭。这里显示 Godot <-> 后端 <-> 角色移动的实时状态。")
	lines.append("backend_connected=%s  socket=%s  url=%s" % [str(backend_connected), _socket_state_name(), backend_url])
	if status_label != null:
		lines.append("status=%s" % status_label.text)
	lines.append("targets seat=%s idle=%s roam=%s total=%s" % [
		str(debug_target_counts.get("seat", 0)),
		str(debug_target_counts.get("idle", 0)),
		str(debug_target_counts.get("roam", 0)),
		str(debug_target_counts.get("total", 0)),
	])
	lines.append("")
	lines.append("Workers")
	var worker_ids := worker_nodes.keys()
	worker_ids.sort()
	for worker_id in worker_ids:
		lines.append(_debug_worker_line(str(worker_id)))
	lines.append("")
	lines.append("Sent")
	lines.append(_format_debug_log(debug_sent_log))
	lines.append("")
	lines.append("Received")
	lines.append(_format_debug_log(debug_received_log))
	debug_label.text = "\n".join(lines)


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

	var snapshot: Dictionary = {}
	var snapshot_value: Variant = agent_snapshots.get(worker_id, {})
	if snapshot_value is Dictionary:
		snapshot = snapshot_value as Dictionary

	var context: Dictionary = {}
	var context_value: Variant = agent_work_contexts.get(worker_id, {})
	if context_value is Dictionary:
		context = context_value as Dictionary

	var mode := str(speech_modes.get(worker_id, "hidden"))
	var pending := str(pending_say_by_worker.has(worker_id))
	return "%s pos=%s %s status=%s backend_target=%s travel=%s bubble=%s pending=%s" % [
		worker_id,
		position_text,
		move_text,
		str(snapshot.get("status", "")),
		str(context.get("target_id", snapshot.get("last_target_id", ""))),
		str(snapshot.get("travel_mode", "")),
		mode,
		pending,
	]


func _socket_state_name() -> String:
	match socket.get_ready_state():
		WebSocketPeer.STATE_CONNECTING:
			return "CONNECTING"
		WebSocketPeer.STATE_OPEN:
			return "OPEN"
		WebSocketPeer.STATE_CLOSING:
			return "CLOSING"
		WebSocketPeer.STATE_CLOSED:
			return "CLOSED"
		_:
			return str(socket.get_ready_state())


func _push_debug_log(log: Array[String], text: String) -> void:
	var compact_text := _shorten_text(text, 260)
	if compact_text.begins_with("stream_delta "):
		return
	if !log.is_empty() and log[-1] == compact_text:
		return
	log.append(compact_text)
	if log.size() > 24:
		log.remove_at(0)


func _format_debug_log(log: Array[String]) -> String:
	if log.is_empty():
		return "- empty"
	var lines: Array[String] = []
	for item in log:
		lines.append("- %s" % item)
	return "\n".join(lines)


func _debug_payload_summary(payload: Dictionary) -> String:
	var payload_value: Variant = payload.get("payload", {})
	var detail := ""
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		if payload_dict.has("text"):
			detail = " text=%s" % _shorten_text(str(payload_dict.get("text", "")), 80)
		elif payload_dict.has("targets") and payload_dict["targets"] is Array:
			detail = " targets=%s" % str((payload_dict["targets"] as Array).size())
	return "%s worker=%s%s" % [
		str(payload.get("type", "")),
		str(payload.get("worker_id", "")),
		detail,
	]


func _debug_command_summary(command: Dictionary) -> String:
	var action := str(command.get("action", ""))
	var worker_id := str(command.get("worker_id", ""))
	var target_id := str(command.get("target_id", ""))
	var say := str(command.get("say", ""))
	var payload_value: Variant = command.get("payload", {})
	var status := ""
	var travel_mode := ""
	var memory_count := 0
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		status = str(payload_dict.get("status", ""))
		travel_mode = str(payload_dict.get("travel_mode", ""))
		var memory_value: Variant = payload_dict.get("memory", [])
		if memory_value is Array:
			memory_count = (memory_value as Array).size()
	return "%s worker=%s target=%s travel=%s status=%s say=%s memory=%s" % [
		action,
		worker_id,
		target_id,
		travel_mode,
		_shorten_text(status, 40),
		_shorten_text(say, 60),
		str(memory_count),
	]


func _create_speech_bubbles() -> void:
	for worker_id in worker_nodes.keys():
		var worker := worker_nodes[worker_id] as Node2D
		if worker == null:
			continue

		var panel := PanelContainer.new()
		panel.name = "SpeechBubble"
		panel.visible = false
		panel.z_index = 200
		panel.z_as_relative = false
		panel.position = pixel_ui_theme.speech_bubble_offset
		panel.custom_minimum_size = pixel_ui_theme.speech_bubble_size
		panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_apply_speech_bubble_style(panel)
		worker.add_child(panel)

		var label := Label.new()
		label.custom_minimum_size = Vector2(pixel_ui_theme.speech_bubble_size.x - 8.0, 0)
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.add_theme_font_size_override("font_size", pixel_ui_theme.speech_font_size)
		label.add_theme_color_override("font_color", pixel_ui_theme.speech_text_color)
		panel.add_child(label)

		speech_panels[worker_id] = panel
		speech_labels[worker_id] = label
		speech_full_text[worker_id] = ""
		speech_visible_chars[worker_id] = 0.0
		speech_modes[worker_id] = "hidden"
		speech_hold_timers[worker_id] = 0.0
		speech_dot_timers[worker_id] = 0.0


func _show_thinking(worker_id: String) -> void:
	if worker_id.is_empty() or !speech_panels.has(worker_id):
		return
	var panel := speech_panels[worker_id] as PanelContainer
	var label := speech_labels[worker_id] as Label
	if panel == null or label == null:
		return
	panel.visible = true
	label.text = "."
	speech_modes[worker_id] = "thinking"
	speech_hold_timers[worker_id] = 0.0
	speech_dot_timers[worker_id] = 0.0


func _show_speech(worker_id: String, text: String) -> void:
	if worker_id.is_empty() or !speech_panels.has(worker_id):
		return
	text = text.strip_edges()
	if text.is_empty():
		return
	var panel := speech_panels[worker_id] as PanelContainer
	var label := speech_labels[worker_id] as Label
	if panel == null or label == null:
		return
	panel.visible = true
	label.text = ""
	speech_full_text[worker_id] = text
	speech_visible_chars[worker_id] = 0.0
	speech_modes[worker_id] = "speaking"
	speech_hold_timers[worker_id] = 0.0


func _clear_all_speech_bubbles() -> void:
	pending_say_by_worker.clear()
	for worker_id in speech_panels.keys():
		speech_modes[worker_id] = "hidden"
		speech_full_text[worker_id] = ""
		speech_visible_chars[worker_id] = 0.0
		speech_hold_timers[worker_id] = 0.0
		var panel := speech_panels[worker_id] as PanelContainer
		if panel != null:
			panel.visible = false


func _update_speech_bubbles(delta: float) -> void:
	for worker_id in speech_panels.keys():
		var panel := speech_panels[worker_id] as PanelContainer
		var label := speech_labels[worker_id] as Label
		if panel == null or label == null:
			continue

		var mode := str(speech_modes.get(worker_id, "hidden"))
		if mode == "hidden":
			panel.visible = false
		elif mode == "thinking":
			panel.visible = true
			var dot_timer := float(speech_dot_timers.get(worker_id, 0.0)) + delta
			if dot_timer >= pixel_ui_theme.thinking_dot_seconds:
				dot_timer = 0.0
				var dot_count := (label.text.length() % 3) + 1
				label.text = _repeat_text(".", dot_count)
			speech_dot_timers[worker_id] = dot_timer
		elif mode == "speaking":
			panel.visible = true
			var full_text := str(speech_full_text.get(worker_id, ""))
			var visible_count := float(speech_visible_chars.get(worker_id, 0.0))
			if visible_count < float(full_text.length()):
				visible_count = minf(float(full_text.length()), visible_count + pixel_ui_theme.speech_type_chars_per_second * delta)
				speech_visible_chars[worker_id] = visible_count
				label.text = full_text.substr(0, int(visible_count))
			else:
				label.text = full_text
				var hold_time := float(speech_hold_timers.get(worker_id, 0.0)) + delta
				speech_hold_timers[worker_id] = hold_time
				if hold_time >= pixel_ui_theme.speech_hold_seconds:
					speech_modes[worker_id] = "hidden"


func _apply_speech_bubble_style(panel: PanelContainer) -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = pixel_ui_theme.speech_bg_color
	style.border_color = pixel_ui_theme.speech_border_color
	style.set_border_width_all(pixel_ui_theme.speech_border_width)
	style.set_corner_radius_all(pixel_ui_theme.corner_radius)
	style.set_content_margin(SIDE_LEFT, 4.0)
	style.set_content_margin(SIDE_TOP, 3.0)
	style.set_content_margin(SIDE_RIGHT, 4.0)
	style.set_content_margin(SIDE_BOTTOM, 3.0)
	panel.add_theme_stylebox_override("panel", style)


func _repeat_text(text: String, count: int) -> String:
	var output := ""
	for _index in range(count):
		output += text
	return output


func _create_boss_ui() -> void:
	# 运行时创建轻量 UI，避免场景里混入和办公室布置无关的节点引用。
	var layer := CanvasLayer.new()
	layer.name = "BossCommandLayer"
	add_child(layer)

	var panel := PanelContainer.new()
	panel.name = "BossCommandPanel"
	panel.set_anchors_preset(Control.PRESET_TOP_LEFT)
	panel.position = pixel_ui_theme.boss_panel_position
	panel.custom_minimum_size = pixel_ui_theme.boss_panel_size
	_apply_pixel_panel_style(panel)
	layer.add_child(panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", pixel_ui_theme.row_separation)
	panel.add_child(box)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", pixel_ui_theme.row_separation)
	box.add_child(row)

	command_input = LineEdit.new()
	command_input.placeholder_text = "输入老板指令"
	command_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	command_input.text_submitted.connect(_on_boss_command_submitted)
	_apply_pixel_line_edit_style(command_input)
	row.add_child(command_input)

	var send_button := Button.new()
	send_button.text = "发送"
	send_button.pressed.connect(_on_send_boss_command_pressed)
	_apply_pixel_button_style(send_button)
	row.add_child(send_button)

	status_label = Label.new()
	status_label.text = "等待后端连接"
	status_label.add_theme_font_size_override("font_size", pixel_ui_theme.ui_font_size)
	box.add_child(status_label)


func _create_hover_detail_ui() -> void:
	var layer := CanvasLayer.new()
	layer.name = "WorkerHoverLayer"
	add_child(layer)

	detail_panel = PanelContainer.new()
	detail_panel.name = "WorkerDetailPanel"
	detail_panel.visible = false
	detail_panel.custom_minimum_size = pixel_ui_theme.detail_panel_size
	_apply_pixel_panel_style(detail_panel)
	layer.add_child(detail_panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", pixel_ui_theme.row_separation)
	detail_panel.add_child(box)

	var tabs := HBoxContainer.new()
	tabs.add_theme_constant_override("separation", pixel_ui_theme.row_separation)
	box.add_child(tabs)
	_add_detail_tab_button(tabs, "profile", "档案")
	_add_detail_tab_button(tabs, "stream", "思考")
	_add_detail_tab_button(tabs, "task", "任务")
	_add_detail_tab_button(tabs, "memory", "记忆")

	detail_label = Label.new()
	detail_label.custom_minimum_size = Vector2(pixel_ui_theme.detail_panel_size.x - pixel_ui_theme.panel_margin_left - pixel_ui_theme.panel_margin_right, 0)
	detail_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	detail_label.add_theme_font_size_override("font_size", pixel_ui_theme.detail_font_size)
	detail_label.add_theme_color_override("font_color", pixel_ui_theme.detail_text_color)
	box.add_child(detail_label)
	_refresh_detail_tab_buttons()


func _add_detail_tab_button(parent: HBoxContainer, tab_id: String, title: String) -> void:
	var button := Button.new()
	button.text = title
	button.toggle_mode = true
	button.pressed.connect(_set_detail_tab.bind(tab_id))
	_apply_pixel_button_style(button)
	parent.add_child(button)
	detail_tab_buttons[tab_id] = button


func _set_detail_tab(tab_id: String) -> void:
	active_detail_tab = tab_id
	_refresh_detail_tab_buttons()


func _refresh_detail_tab_buttons() -> void:
	for tab_id in detail_tab_buttons.keys():
		var button := detail_tab_buttons[tab_id] as Button
		if button == null:
			continue
		button.button_pressed = tab_id == active_detail_tab


func _update_hover_detail() -> void:
	if detail_panel == null:
		return

	var over_detail_panel := _is_mouse_over_detail_panel()
	hovered_worker = _find_hovered_worker()
	if pinned_worker != null:
		focused_worker = pinned_worker
	elif over_detail_panel and focused_worker != null:
		pass
	elif hovered_worker != null:
		focused_worker = hovered_worker
	else:
		focused_worker = null

	if focused_worker == null:
		detail_panel.visible = false
		return

	var worker_id := str(focused_worker.name)
	detail_label.text = _build_worker_detail_text(worker_id)
	if pinned_worker == null and hovered_worker != null:
		_position_detail_panel()
	detail_panel.visible = true


func _find_hovered_worker() -> Node2D:
	var mouse_position := get_global_mouse_position()
	var closest_worker: Node2D = null
	var closest_distance := worker_hover_radius
	for node in get_tree().get_nodes_in_group(worker_group):
		if !(node is Node2D):
			continue

		var worker := node as Node2D
		var distance := worker.global_position.distance_to(mouse_position)
		if distance <= closest_distance:
			closest_distance = distance
			closest_worker = worker
	return closest_worker


func _position_detail_panel() -> void:
	var mouse_screen_position := get_viewport().get_mouse_position()
	var viewport_size := get_viewport_rect().size
	var panel_size := detail_panel.size
	if panel_size.x <= 1.0:
		panel_size = detail_panel.custom_minimum_size

	var panel_position := mouse_screen_position + pixel_ui_theme.detail_panel_offset
	if panel_position.x + panel_size.x > viewport_size.x:
		panel_position.x = mouse_screen_position.x - panel_size.x - pixel_ui_theme.detail_panel_offset.x
	if panel_position.y + panel_size.y > viewport_size.y:
		panel_position.y = viewport_size.y - panel_size.y - pixel_ui_theme.viewport_padding
	panel_position.x = maxf(pixel_ui_theme.viewport_padding, panel_position.x)
	panel_position.y = maxf(pixel_ui_theme.viewport_padding, panel_position.y)
	detail_panel.position = panel_position


func _is_mouse_over_detail_panel() -> bool:
	if detail_panel == null or !detail_panel.visible:
		return false
	var mouse_position := get_viewport().get_mouse_position()
	var panel_rect := Rect2(detail_panel.global_position, detail_panel.size)
	if panel_rect.size.x <= 1.0 or panel_rect.size.y <= 1.0:
		panel_rect.size = detail_panel.custom_minimum_size
	return panel_rect.has_point(mouse_position)


func _build_worker_detail_text(worker_id: String) -> String:
	var snapshot_value: Variant = agent_snapshots.get(worker_id, {})
	if !(snapshot_value is Dictionary):
		return _build_local_profile_text(worker_id)

	var snapshot: Dictionary = snapshot_value as Dictionary
	if snapshot.is_empty():
		return _build_local_profile_text(worker_id)

	var context: Dictionary = {}
	var context_value: Variant = agent_work_contexts.get(worker_id, {})
	if context_value is Dictionary:
		context = context_value as Dictionary

	var lines: Array[String] = []
	if active_detail_tab == "profile":
		lines.append("%s  %s" % [str(snapshot.get("name", worker_id)), str(snapshot.get("role", ""))])
		lines.append("状态: %s / %s" % [str(snapshot.get("status", "")), str(snapshot.get("mood", ""))])
		lines.append("精力 %.2f  压力 %.2f  循环 %s" % [
			float(snapshot.get("energy", 0.0)),
			float(snapshot.get("stress", 0.0)),
			str(snapshot.get("autonomy_steps", 0)),
		])
		lines.append("沟通: %s" % str(snapshot.get("communication_style", "")))
		lines.append("重视: %s" % _join_string_array(snapshot.get("work_values", []), " / "))
		lines.append("性格: %s" % str(snapshot.get("personality", "")))
	elif active_detail_tab == "stream":
		lines.append("%s 的现场思考流" % str(snapshot.get("name", worker_id)))
		lines.append(_format_stream_lines(worker_id))
	elif active_detail_tab == "task":
		lines.append("当前任务: %s" % str(snapshot.get("focus_task", "")))
		if !context.is_empty():
			lines.append("意图: %s" % str(context.get("intent", "")))
			lines.append("推进: %s" % str(context.get("work_update", "")))
			lines.append("信心: %.2f" % float(context.get("confidence", 0.0)))
		var risk := str(snapshot.get("current_risk", ""))
		if !risk.is_empty():
			lines.append("风险: %s" % risk)
		var helper := str(snapshot.get("needs_help_from", ""))
		if !helper.is_empty():
			lines.append("协作对象: %s" % helper)
		var question := str(snapshot.get("confirmation_question", ""))
		if !question.is_empty():
			lines.append("内部待确认: %s" % question)
	elif active_detail_tab == "memory":
		lines.append("短期记忆")
		var memory_text := _format_recent_memory(snapshot.get("memory", []))
		lines.append(memory_text if !memory_text.is_empty() else "暂无新的工作记忆。")

	var last_say := str(snapshot.get("last_say", ""))
	if active_detail_tab == "stream" and !last_say.is_empty():
		lines.append("台词: %s" % last_say)
	return "\n".join(lines)


func _build_local_profile_text(worker_id: String) -> String:
	var profile: Dictionary = local_worker_profiles.get(worker_id, {"name": worker_id, "role": "员工"})
	return "%s  %s\n等待后端画像\n\n后端连接后会显示完整人设、任务、风险、协作和记忆。\n提示词目录: %s" % [
		str(profile.get("name", worker_id)),
		str(profile.get("role", "员工")),
		backend_agent_prompts_dir,
	]


func _shorten_text(text: String, max_length: int) -> String:
	if text.length() <= max_length:
		return text
	return text.substr(0, max_length) + "..."


func _join_string_array(value: Variant, separator: String) -> String:
	if !(value is Array):
		return ""

	var parts: Array[String] = []
	for item in value:
		parts.append(str(item))
	return separator.join(parts)


func _format_recent_memory(value: Variant) -> String:
	if !(value is Array):
		return ""

	var memory: Array = value
	var start_index := maxi(0, memory.size() - 3)
	var lines: Array[String] = []
	for index in range(start_index, memory.size()):
		var line := str(memory[index])
		if line.contains("LLM目标无效") or line.begins_with("LLM决策:") or line.begins_with("规则决策:"):
			continue
		line = line.replace("工作记忆:", "")
		lines.append("- %s" % line)
	return "\n".join(lines)


func _format_stream_lines(worker_id: String) -> String:
	var value: Variant = agent_streams.get(worker_id, [])
	if !(value is Array):
		return "正在观察办公室，还没有新的思考流。"

	var stream: Array = value
	if stream.is_empty():
		return "正在观察办公室，还没有新的思考流。"

	var start_index := maxi(0, stream.size() - 8)
	var lines: Array[String] = []
	for index in range(start_index, stream.size()):
		lines.append("> %s" % str(stream[index]))
	return "\n".join(lines)


func _apply_pixel_panel_style(panel: PanelContainer) -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = pixel_ui_theme.panel_bg_color
	style.border_color = pixel_ui_theme.panel_border_color
	style.set_border_width_all(pixel_ui_theme.panel_border_width)
	style.set_corner_radius_all(pixel_ui_theme.corner_radius)
	style.set_content_margin(SIDE_LEFT, pixel_ui_theme.panel_margin_left)
	style.set_content_margin(SIDE_TOP, pixel_ui_theme.panel_margin_top)
	style.set_content_margin(SIDE_RIGHT, pixel_ui_theme.panel_margin_right)
	style.set_content_margin(SIDE_BOTTOM, pixel_ui_theme.panel_margin_bottom)
	panel.add_theme_stylebox_override("panel", style)


func _apply_pixel_line_edit_style(line_edit: LineEdit) -> void:
	line_edit.add_theme_font_size_override("font_size", pixel_ui_theme.ui_font_size)
	line_edit.add_theme_color_override("font_color", pixel_ui_theme.text_color)
	line_edit.add_theme_color_override("font_placeholder_color", pixel_ui_theme.placeholder_color)
	var normal := _make_pixel_box(pixel_ui_theme.field_bg_color, pixel_ui_theme.field_border_color, pixel_ui_theme.field_border_width)
	var focus := _make_pixel_box(pixel_ui_theme.field_focus_bg_color, pixel_ui_theme.field_focus_border_color, pixel_ui_theme.field_focus_border_width)
	line_edit.add_theme_stylebox_override("normal", normal)
	line_edit.add_theme_stylebox_override("focus", focus)


func _apply_pixel_button_style(button: Button) -> void:
	button.add_theme_font_size_override("font_size", pixel_ui_theme.ui_font_size)
	button.add_theme_color_override("font_color", pixel_ui_theme.text_color)
	button.add_theme_stylebox_override("normal", _make_pixel_box(pixel_ui_theme.button_bg_color, pixel_ui_theme.button_border_color, pixel_ui_theme.field_border_width))
	button.add_theme_stylebox_override("hover", _make_pixel_box(pixel_ui_theme.button_hover_bg_color, pixel_ui_theme.button_active_border_color, pixel_ui_theme.field_border_width))
	button.add_theme_stylebox_override("pressed", _make_pixel_box(pixel_ui_theme.button_pressed_bg_color, pixel_ui_theme.button_active_border_color, pixel_ui_theme.field_focus_border_width))


func _make_pixel_box(bg_color: Color, border_color: Color, border_width: int) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = bg_color
	style.border_color = border_color
	style.set_border_width_all(border_width)
	style.set_corner_radius_all(pixel_ui_theme.corner_radius)
	style.set_content_margin(SIDE_LEFT, pixel_ui_theme.content_margin_left)
	style.set_content_margin(SIDE_TOP, pixel_ui_theme.content_margin_top)
	style.set_content_margin(SIDE_RIGHT, pixel_ui_theme.content_margin_right)
	style.set_content_margin(SIDE_BOTTOM, pixel_ui_theme.content_margin_bottom)
	return style


func _on_send_boss_command_pressed() -> void:
	_submit_boss_command()


func _on_boss_command_submitted(_text: String) -> void:
	_submit_boss_command()


func _submit_boss_command() -> void:
	if command_input == null:
		return

	var text := command_input.text.strip_edges()
	if text.is_empty():
		return
	send_boss_command(text)
	command_input.clear()


func _set_status(text: String) -> void:
	if status_label != null:
		status_label.text = text
