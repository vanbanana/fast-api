extends Node2D
## Demo 入口编排器：场景目标同步、后端命令分发、组件装配。
## WebSocket / 气泡 / 详情面板 / debug 面板 / 老板 UI 拆分在 scenes/components/ 下。

const DEFAULT_PIXEL_UI_THEME := preload("res://ui/pixel_ui_theme.gd")
const DEFAULT_CJK_FONT := preload("res://fonts/simhei.ttf")
const BackendClient := preload("res://scenes/components/backend_client.gd")
const SpeechBubbles := preload("res://scenes/components/speech_bubbles.gd")
const DetailPanel := preload("res://scenes/components/detail_panel.gd")
const DebugPanel := preload("res://scenes/components/debug_panel.gd")
const PhoneUi := preload("res://scenes/components/phone_ui.gd")
const AgentStateStore := preload("res://scenes/components/agent_state_store.gd")
const TokenBar := preload("res://scenes/components/token_bar.gd")

@export_category("后端连接")
@export var backend_enabled: bool = true
@export var backend_url: String = "ws://127.0.0.1:8000/ws/office"
@export var auto_detect_web_url: bool = true
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
@export var roam_marker_group: StringName = &"roam_markers"

var backend: BackendClient
var speech_bubbles: SpeechBubbles
var detail_panel: DetailPanel
var debug_panel: DebugPanel
var a2a_controller: A2AController = null
var phone_ui: PhoneUi
var token_bar: TokenBar
var agent_store := AgentStateStore.new()

# 氛围层定时器：定期向后端请求台词/状态文本
var atmosphere_timer: Timer = null

var worker_nodes: Dictionary = {}
var pending_say_by_worker: Dictionary = {}
var _last_speech_text: Dictionary = {}  # worker_id -> 上次气泡文本，去重用
var meeting_say_done_timers: Dictionary = {}
var _reset_button: Button = null
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
		# 交互系统：面对面交谈事件
		if worker.has_signal(&"chat_started"):
			worker.chat_started.connect(_on_worker_chat_started)

	if boss_ui_enabled:
		phone_ui = PhoneUi.new()
		phone_ui.name = "PhoneUi"
		add_child(phone_ui)
		phone_ui.setup(pixel_ui_theme, local_worker_profiles)
		phone_ui.command_submitted.connect(send_boss_command)

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

	# 初始化 A2A 控制器
	a2a_controller = A2AController.new()
	add_child(a2a_controller)
	a2a_controller.worker_nodes = worker_nodes
	a2a_controller.speech_bubbles = speech_bubbles
	a2a_controller.phone_ui = phone_ui
	a2a_controller.debug_panel = debug_panel
	a2a_controller.a2a_event_requested.connect(_on_a2a_event_requested)

	# 左上角重启按钮
	_reset_button = Button.new()
	_reset_button.name = "ResetButton"
	_reset_button.text = "重启"
	_reset_button.position = Vector2(8, 8)
	_reset_button.size = Vector2(60, 28)
	_reset_button.mouse_filter = Control.MOUSE_FILTER_STOP
	var _reset_layer := CanvasLayer.new()
	_reset_layer.name = "ResetLayer"
	_reset_layer.layer = 100
	add_child(_reset_layer)
	_reset_layer.add_child(_reset_button)
	_reset_button.pressed.connect(_on_reset_button_pressed)

	if backend_enabled:
		# Web 导出时自动检测后端地址（使用当前页面的 host）
		if auto_detect_web_url and OS.has_feature("web"):
			var js_host: String = str(JavaScriptBridge.eval("window.location.host", true))
			if js_host != "" and js_host != "null":
				var ws_protocol: String = "ws"
				var js_proto: String = str(JavaScriptBridge.eval("window.location.protocol", true))
				if js_proto == "https:":
					ws_protocol = "wss"
				backend_url = "%s://%s/ws/office" % [ws_protocol, js_host]
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

	# 初始化氛围层定时器：每 10 秒向后端请求一次台词/状态文本
	_setup_atmosphere_timer()

	# Web 导出中文字体：只给 Label 设置字体覆盖，不影响颜色/样式
	if OS.has_feature("web"):
		var cjk_font := load("res://fonts/simhei.ttf") as FontFile
		if cjk_font != null:
			for node in get_tree().root.find_children("*", "Label", true, false):
				if node is Label:
					node.add_theme_font_override(&"font", cjk_font)


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
	# 清除旧缓存，避免后端重启后残留的"在办公室待命"等脏数据
	agent_store.clear()
	# 后端重启后任务数据全丢，彻底重置所有worker状态
	for wid in worker_nodes:
		var worker = worker_nodes[wid]
		if worker == null:
			continue
		# 清任务绑定
		if "has_active_task" in worker:
			worker.set("has_active_task", false)
		if "current_task_id" in worker:
			worker.set("current_task_id", "")
		# 强制状态机回IDLE
		var sm = worker.get("state_machine")
		if sm != null and sm.has_method(&"force_idle"):
			sm.force_idle()
		# 清气泡
		if speech_bubbles != null:
			speech_bubbles.set_status(wid, "")
	_set_workers_external_decision(true)
	_send_world_snapshot()
	_set_status("后端已连接")
	# 后端连接成功后启动氛围层请求
	if atmosphere_timer != null:
		atmosphere_timer.start()
	# 测试接口已移除，正式走完整会议流程


## 重启按钮：归零所有状态（前端 + 后端），适配 Web 导出。
func _on_reset_button_pressed() -> void:
	if debug_panel != null:
		debug_panel.log_received("[RESET] 开始重置...")
	# 1. 取消所有进行中的 A2A 对话
	if a2a_controller != null:
		a2a_controller.cancel_all_chats()
	# 2. 清除所有前端缓存和状态
	agent_store.clear()
	_last_speech_text.clear()
	pending_say_by_worker.clear()
	# 清除会议 say_done 计时器
	for key in meeting_say_done_timers:
		var t := meeting_say_done_timers[key] as Timer
		if t != null:
			t.queue_free()
	meeting_say_done_timers.clear()
	# 3. 重置所有 worker 节点
	for wid in worker_nodes:
		var worker = worker_nodes[wid]
		if worker == null:
			continue
		if "has_active_task" in worker:
			worker.set("has_active_task", false)
		if "current_task_id" in worker:
			worker.set("current_task_id", "")
		if "errand_directive_text" in worker:
			worker.set("errand_directive_text", "")
		var sm = worker.get("state_machine")
		if sm != null and sm.has_method(&"force_idle"):
			sm.force_idle()
		if worker.has_method(&"cancel_seeking"):
			worker.cancel_seeking()
	# 4. 清除气泡
	if speech_bubbles != null:
		speech_bubbles.clear_all()
	# 5. 清除手机面板任务数据
	if phone_ui != null and phone_ui.has_method(&"clear_tasks"):
		phone_ui.clear_tasks()
	# 6. 断开后端并重连（重连后 _on_backend_connected 会清缓存 + 发 world_snapshot → 后端重置）
	if backend != null:
		backend.connect_backend()
	_set_status("已重置，等待后端连接...")


## [已废弃] 调用后端测试接口（接口已删除）
func _fetch_test_meeting_data() -> void:
	var http := HTTPRequest.new()
	http.request_completed.connect(_on_test_meeting_response.bind(http))
	add_child(http)
	var error := http.request(
		"http://127.0.0.1:8000/test/simulate-meeting-finish",
		[],
		HTTPClient.METHOD_POST,
		JSON.stringify({"topic": "开发单词卡片App核心功能"}),
	)
	if error != OK:
		push_warning("请求测试会议数据失败: %s" % error)
		http.queue_free()


func _on_test_meeting_response(result: int, response_code: int, headers: PackedStringArray, body: PackedByteArray, http: HTTPRequest) -> void:
	http.queue_free()
	if result != HTTPRequest.RESULT_SUCCESS or response_code != 200:
		push_warning("测试会议数据返回异常: %d %d" % [result, response_code])
		return
	var json := JSON.new()
	if json.parse(body.get_string_from_utf8()) != OK:
		push_warning("解析测试会议数据失败")
		return
	var data := json.data as Dictionary
	if debug_panel != null:
		debug_panel.log_received("[TEST] 收到模拟会议数据，%d个任务" % data.size())
	# 直接当作 task_update 命令处理，推送到手机面板
	_apply_task_update_command(data)


func _on_backend_disconnected() -> void:
	_set_workers_external_decision(false)
	_set_status("后端断开，角色使用本地逻辑")
	# 断开后端时停止氛围层定时器
	if atmosphere_timer != null:
		atmosphere_timer.stop()


func _on_backend_parse_failed(text: String) -> void:
	if debug_panel != null:
		debug_panel.log_received("PARSE_FAILED %s" % text)


func _on_backend_message_sent(summary: String) -> void:
	if debug_panel != null:
		debug_panel.log_sent(summary)


## A2A 控制器的事件转发：将 A2A 事件发送到后端。
func _on_a2a_event_requested(event_data: Dictionary) -> void:
	if _backend_connected():
		backend.send_json(event_data)


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
	# 空闲决策不再发后端（省 token）。
	# 只在老板指令 / 找人交互等有意义的场景才调 LLM。
	if worker.has_method(&"wait_for_next_decision"):
		worker.wait_for_next_decision()


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


## 通用事件发送接口（供 worker 节点调用，如 task_progress）。
func send_event_to_backend(event_data: Dictionary) -> void:
	if backend == null:
		return
	backend.send_json(event_data)
	if debug_panel != null:
		debug_panel.log_sent("EVENT %s" % str(event_data.get("type", "?")))


func _apply_agent_command(command: Dictionary) -> void:
	var action := str(command.get("action", ""))
	var worker_id := str(command.get("worker_id", ""))
	if debug_panel != null:
		debug_panel.log_received_command(command)
	if action == "token_usage":
		var usage_value: Variant = command.get("payload", {})
		if token_bar != null and usage_value is Dictionary:
			token_bar.update_usage(usage_value as Dictionary)
		return

	if action == "llm_log":
		if debug_panel != null:
			var payload_value: Variant = command.get("payload", {})
			if payload_value is Dictionary:
				var payload := payload_value as Dictionary
				debug_panel.log_llm_call(
					str(payload.get("tag", "?")),
					int(payload.get("tokens_in", 0)),
					int(payload.get("tokens_out", 0)),
					str(payload.get("result", "")),
				)
		return

	agent_store.cache_command(command)
	# meeting_finished 的 move_to 显示"返回工位中"，其他正常从快照更新
	var cmd_travel_mode := ""
	var cmd_payload: Variant = command.get("payload", {})
	if cmd_payload is Dictionary:
		cmd_travel_mode = str((cmd_payload as Dictionary).get("travel_mode", ""))
	if action == "move_to" and cmd_travel_mode == "meeting_finished":
		update_worker_status(str(command.get("worker_id", "")), "返回工位中")
	else:
		_update_status_bubble(str(command.get("worker_id", "")))

	# --- 氛围层：只处理后端返回的文本类指令 ---
	if action == "say":
		_apply_say_command(command)
		return

	if action == "errand_seek":
		_apply_errand_seek_command(command)
		return

	if action == "idle":
		_apply_idle_command(command)
		return

	if action == "atmosphere_response":
		_apply_atmosphere_response(command)
		return

	# --- A2A 对话指令（AutoGen Two-Agent Chat 模式）---
	if action == "chat_line":
		if a2a_controller != null:
			a2a_controller.apply_chat_line(command)
		return

	if action == "chat_end":
		if a2a_controller != null:
			a2a_controller.apply_chat_end(command)
		return

	if action == "chat_canceled":
		if a2a_controller != null:
			a2a_controller.apply_chat_canceled(command)
		return

	# --- 移动指令 ---
	if action == "move_to":
		var target_id := StringName(str(command.get("target_id", "")))
		var payload_value: Variant = command.get("payload", {})
		var travel_mode := "normal"
		if payload_value is Dictionary:
			var payload_dict := payload_value as Dictionary
			travel_mode = str(payload_dict.get("travel_mode", "normal"))

		if travel_mode in ["meeting", "force_seat"]:
			# 去会议室入座
			_apply_move_to_meeting_or_force(command, worker_id, target_id, travel_mode)
		elif travel_mode == "meeting_finished":
			# 会议结束，回工位
			_apply_move_to_desk(command, worker_id, target_id)
		else:
			if debug_panel != null:
				debug_panel.log_received("MOVE_LOCAL_OVERRIDDEN %s -> %s mode=%s" % [worker_id, str(target_id), travel_mode])
		return

	# --- 状态更新指令（已落座等）---
	if action == "status":
		_apply_status_command(command, worker_id)
		return

	if action == "task_update":
		_apply_task_update_command(command)
		return

	# 其他未知 action 忽略
	if !action.is_empty() and debug_panel != null:
		debug_panel.log_received("UNKNOWN_ACTION %s" % action)


## 处理会议/强制入座类型的移动指令（保留原有逻辑）。
func _apply_move_to_meeting_or_force(command: Dictionary, worker_id: String, target_id: StringName, travel_mode: String) -> void:
	if worker_id.is_empty() or str(target_id).is_empty():
		return

	var workers := get_node_or_null(^"workers")
	if workers == null:
		return

	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker == null:
		return

	var moved: bool = false
	if travel_mode == "meeting" and worker.has_method(&"force_seat_marker_id"):
		moved = worker.force_seat_marker_id(target_id)
	elif travel_mode == "force_seat" and worker.has_method(&"force_seat_marker_id"):
		moved = worker.force_seat_marker_id(target_id)

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


## 会议结束，导航回工位。
func _apply_move_to_desk(command: Dictionary, worker_id: String, target_id: StringName) -> void:
	if worker_id.is_empty() or str(target_id).is_empty():
		return

	var workers := get_node_or_null(^"workers")
	if workers == null:
		return

	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker == null:
		return

	var say := str(command.get("say", ""))
	# 先取消会议锁定状态
	if worker.has_method(&"cancel_seeking"):
		worker.cancel_seeking()
	# 关闭外部决策模式，恢复本地状态机自主运行（会议/找人/聊天结束后都需要）
	if worker.has_method(&"set_external_decision_enabled"):
		worker.set_external_decision_enabled(false)
	# 从后端快照同步任务状态（会议可能恢复了被中断的任务或分配了新任务）
	var payload_value: Variant = command.get("payload", {})
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		var task_id := str(payload_dict.get("active_task_id", ""))
		if not task_id.is_empty():
			worker.set("current_task_id", task_id)
			worker.set("has_active_task", true)
		else:
			worker.set("has_active_task", false)
	# 导航回工位
	if worker.has_method(&"return_to_seat"):
		worker.return_to_seat()
	if !say.is_empty():
		speech_bubbles.show_speech(worker_id, say)
	if debug_panel != null:
		debug_panel.log_received("[MEETING_END] %s 回工位" % worker_id)


## 状态更新指令（已落座等）。
func _apply_status_command(command: Dictionary, worker_id: String) -> void:
	var status_text := str(command.get("status", ""))
	var display_name := str(command.get("display_name", ""))
	if status_text.is_empty():
		return
	if !display_name.is_empty():
		speech_bubbles.show_speech(worker_id, "%s [%s]" % [display_name, status_text])
	else:
		speech_bubbles.show_speech(worker_id, "[%s]" % status_text)
	if debug_panel != null:
		debug_panel.log_received("[STATUS] %s → %s" % [worker_id, status_text])


## 处理任务列表更新命令。
func _apply_task_update_command(command: Dictionary) -> void:
	var payload_value: Variant = command.get("payload", {})
	if phone_ui != null and phone_ui.has_method(&"update_task_data"):
		phone_ui.update_task_data(payload_value)
	if debug_panel != null:
		var task_count := 0
		if payload_value is Dictionary:
			var dict := payload_value as Dictionary
			if dict.has("tasks") and dict["tasks"] is Array:
				task_count = (dict["tasks"] as Array).size()
		elif payload_value is Array:
			task_count = (payload_value as Array).size()
		debug_panel.log_received("[TASK_UPDATE] %d 个任务" % task_count)


func _apply_idle_command(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	if worker_id.is_empty() or worker_id == "office":
		return

	var talk_text := str(command.get("say", ""))
	var payload_value: Variant = command.get("payload", {})
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		if str(payload_dict.get("travel_mode", "")) == "meeting":
			return
		if str(payload_dict.get("decision_source", "")) == "profile_update":
			_apply_profile_to_worker(worker_id, payload_dict)
		var sync_task_id := str(payload_dict.get("active_task_id", ""))
		var sync_status := str(payload_dict.get("status", ""))
		var workers_for_sync := get_node_or_null(^"workers")
		if workers_for_sync != null:
			var w := workers_for_sync.get_node_or_null(NodePath(worker_id))
			if w != null:
				if not sync_task_id.is_empty():
					w.set("current_task_id", sync_task_id)
					w.set("has_active_task", true)
				else:
					# 后端明确返回空 active_task_id：任务完成或无任务
					w.set("current_task_id", "")
					w.set("has_active_task", false)

	var workers := get_node_or_null(^"workers")
	if workers == null:
		return

	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker != null:
		if !talk_text.is_empty():
			_show_speech(worker_id, talk_text)
		if worker.has_method(&"wait_for_next_decision"):
			worker.wait_for_next_decision()


## 将后端快照中的角色信息写入 worker 节点，供 atmosphere 请求读取。
func _apply_profile_to_worker(worker_id: String, payload: Dictionary) -> void:
	var workers := get_node_or_null(^"workers")
	if workers == null:
		return
	var worker := workers.get_node_or_null(NodePath(worker_id))
	if worker == null:
		return
	# 写入角色信息字段
	if worker.get("worker_role") != null:
		worker.set("worker_role", str(payload.get("role", "")))
	if worker.get("worker_personality") != null:
		worker.set("worker_personality", str(payload.get("personality", "")))


func _apply_say_command(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	var say := str(command.get("say", ""))
	if worker_id.is_empty():
		return

	var payload_value: Variant = command.get("payload", {})
	var payload: Dictionary = payload_value if payload_value is Dictionary else {}
	if !say.is_empty() and str(payload.get("display", "")) == "speech":
		_show_speech(worker_id, say)

	var session_id := str(payload.get("meeting_session_id", ""))
	if !session_id.is_empty():
		_schedule_meeting_say_done(worker_id, session_id, maxf(1.2, float(say.length()) / 8.0))
		return
	# say 不会触发移动/到达，必须手动重启该员工的决策计时器，否则循环死掉。
	_resume_worker_decision(worker_id)


## 处理 errand_seek 命令：显示台词 + 让员工真正去目标人物身边。
## 注意：不重启 decision 计时器，seeking 状态由交互系统自管理生命周期。
func _apply_errand_seek_command(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	var say := str(command.get("say", ""))
	if worker_id.is_empty():
		return

	# 如果发起人正在对话中，先取消当前对话
	if a2a_controller != null and a2a_controller.is_worker_in_chat(worker_id):
		a2a_controller.cancel_chat_for_worker(worker_id)

	# 显示气泡台词
	if !say.is_empty():
		_show_speech(worker_id, say)

	# 从 payload 中取出目标 worker ID
	var payload_value: Variant = command.get("payload", {})
	var payload: Dictionary = payload_value if payload_value is Dictionary else {}
	var target_worker_id := str(payload.get("errand_target_worker_id", ""))

	if target_worker_id.is_empty():
		# 没有目标信息，回退到普通 say 行为
		_resume_worker_decision(worker_id)
		return

	# 找到两端 worker 节点，调用 seek_worker
	var workers_node := get_node_or_null(^"workers")
	if workers_node == null:
		_resume_worker_decision(worker_id)
		return

	var actor_worker = workers_node.get_node_or_null(NodePath(worker_id))
	var target_worker = workers_node.get_node_or_null(NodePath(target_worker_id))
	if actor_worker == null or target_worker == null:
		if debug_panel != null:
			debug_panel.log_received("ERRAND_SEEK_MISSING worker=%s target=%s" % [worker_id, target_worker_id])
		_resume_worker_decision(worker_id)
		return

	if actor_worker.has_method(&"seek_worker"):
		# 从后端快照同步任务状态（找人时后端已清空 active_task_id）
		var sync_task_id := str(payload.get("active_task_id", ""))
		if sync_task_id.is_empty():
			actor_worker.set("current_task_id", "")
			actor_worker.set("has_active_task", false)
		actor_worker.errand_directive_text = str(payload.get("errand_directive_text", ""))
		var ok: bool = actor_worker.seek_worker(target_worker as Node2D)
		if debug_panel != null:
			debug_panel.log_received("ERRAND_SEEK %s -> %s ok=%s" % [worker_id, target_worker_id, ok])
	else:
		_resume_worker_decision(worker_id)


func _resume_worker_decision(worker_id: String) -> void:
	var worker := worker_nodes.get(worker_id) as Node
	if worker != null and worker.has_method(&"wait_for_next_decision"):
		worker.wait_for_next_decision()


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
	if phone_ui != null:
		var profile: Dictionary = local_worker_profiles.get(worker_id, {})
		phone_ui.add_chat_message(str(profile.get("name", worker_id)), text, false)
	_pause_speakers_for_chat(worker_id, text)


func _pause_speakers_for_chat(worker_id: String, text: String) -> void:
	# 说话的人停下站住；附近的对话对象也停下并面向说话者，像真人交谈。
	# 优先使用 worker 自带的邻近检测结果（Area2D 检测），回退到距离检查。
	var speaker := worker_nodes.get(worker_id) as Node2D
	if speaker == null or !speaker.has_method(&"pause_for_speech"):
		return

	# 使用新的邻近检测系统（按距离排序，取最近的）
	var partner: Node2D = null
	if speaker.has_method(&"get_nearby_workers"):
		var nearby: Array = speaker.get_nearby_workers()
		if nearby.size() > 0:
			# 按距离排序，选最近的那个
			var best_dist := INF
			for n in nearby:
				var d := speaker.global_position.distance_to((n as Node2D).global_position)
				if d < best_dist:
					best_dist = d
					partner = n as Node2D

	# 回退：如果邻近检测没找到人，用距离检查兜底
	if partner == null:
		partner = _nearest_worker_within(speaker, 40.0)

	if partner != null:
		speaker.pause_for_speech(text.length(), partner.global_position)
		if partner.has_method(&"pause_for_speech"):
			partner.pause_for_speech(text.length(), speaker.global_position)
	else:
		speaker.pause_for_speech(text.length())


## 处理 worker 之间的面对面交谈事件（由 seek_worker 找到目标后触发）。
func _on_worker_chat_started(speaker: Node2D, listener: Node2D) -> void:
	if a2a_controller != null:
		a2a_controller.start_chat(speaker, listener)


func _nearest_worker_within(speaker: Node2D, radius: float) -> Node2D:
	var nearest: Node2D = null
	var nearest_distance := radius
	for other in worker_nodes.values():
		var node := other as Node2D
		if node == null or node == speaker:
			continue
		var distance := speaker.global_position.distance_to(node.global_position)
		if distance < nearest_distance:
			nearest_distance = distance
			nearest = node
	return nearest


func _update_status_bubble(worker_id: String) -> void:
	if speech_bubbles == null or worker_id.is_empty() or worker_id == "office":
		return
	var snapshot := agent_store.snapshot_for(worker_id)
	speech_bubbles.set_status(worker_id, str(snapshot.get("status", "")))


## 直接更新某人的状态气泡（不依赖后端快照，供状态机本地切换时调用）。
func update_worker_status(worker_id: String, status_text: String) -> void:
	if speech_bubbles == null or worker_id.is_empty() or worker_id == "office":
		return
	speech_bubbles.set_status(worker_id, status_text)


func _clear_all_speech_bubbles() -> void:
	pending_say_by_worker.clear()
	if speech_bubbles != null:
		speech_bubbles.clear_all()


func _set_status(text: String) -> void:
	if phone_ui != null:
		phone_ui.set_status(text)


func _debug_status_line() -> String:
	var socket_state := "DISABLED"
	if backend != null:
		socket_state = backend.socket_state_name()
	var status := ""
	if phone_ui != null:
		status = phone_ui.status_text()
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
	var worker := worker_nodes.get(worker_id) as DemoWorker
	var snapshot := agent_store.snapshot_for(worker_id)
	var context := agent_store.context_for(worker_id)

	# 取名字（从 snapshot 或 fallback）
	var display_name := str(snapshot.get("name", str(worker.name) if worker else worker_id))
	var status := str(snapshot.get("status", "?"))
	var mood := str(snapshot.get("mood", ""))
	var intent := str(snapshot.get("intent", ""))

	# 气泡内容
	var bubble_text := ""
	if speech_bubbles != null:
		bubble_text = speech_bubbles.text_for(worker_id)

	# 寻路/交互状态
	var extra := ""
	if worker != null and worker.has_method(&"get_debug_state"):
		var state_value: Variant = worker.get_debug_state()
		if state_value is Dictionary:
			var state := state_value as Dictionary
			var sm_state := str(state.get("sm_state", ""))
			if not sm_state.is_empty():
				extra += " [%s]" % sm_state
			if state.get("seek_target", "") != "":
				extra += " →%s" % str(state.get("seek_target", ""))

	var parts := ["%s(%s)" % [display_name, status]]
	if not mood.is_empty():
		parts.append(mood)
	if not intent.is_empty() and intent.length() < 30:
		parts.append(intent)
	if not bubble_text.is_empty():
		parts.append("\"%s\"" % bubble_text.substr(0, 25))
	if not extra.is_empty():
		parts.append(extra)

	return "  ".join(parts)


# ============================================================
#  氛围层定时请求
# ============================================================

## 创建并配置氛围层定时器（已禁用：不显示气泡 = 纯烧 token）。
func _setup_atmosphere_timer() -> void:
	pass  # 氛围系统已关闭——不冒气泡就没必要调 LLM


## 氛围定时器触发：每次随机选 3-4 个 worker 请求氛围台词（省 token）。
func _on_atmosphere_timer_timeout() -> void:
	if !_backend_connected() or backend == null:
		return

	# 收集所有可用 worker ID
	var all_ids: Array[String] = []
	for worker_id in worker_nodes:
		all_ids.append(worker_id)
	if all_ids.is_empty():
		return

	# 随机打乱，每次只取前 4 个（或全部不足 4 个）
	all_ids.shuffle()
	var batch_size := mini(4, all_ids.size())
	var selected_ids: Array[String] = []
	for i in range(batch_size):
		selected_ids.append(all_ids[i])

	var worker_requests: Array[Dictionary] = []
	for worker_id in selected_ids:
		var worker: Node = worker_nodes[worker_id] as Node
		if worker == null:
			continue
		# 收集完整的状态数据供后端生成氛围内容
		var sm_state := "idle"
		var sm = worker.get("state_machine")
		if sm != null and sm.has_method(&"get_state_name"):
			match sm.get_state_name():
				"WORKING":
					sm_state = "working"
				"BREAK":
					sm_state = "break"
				"ROAMING":
					sm_state = "roaming"
				"SEEKING":
					sm_state = "seeking"
				"CHATTING":
					sm_state = "chatting"
				_:
					sm_state = "idle"
		# 收集附近同事名字供后端生成有上下文的氛围内容
		var nearby_names: Array[String] = []
		if worker.has_method(&"get_nearby_workers"):
			var nearby: Array = worker.get_nearby_workers()
			for n in nearby:
				nearby_names.append(str(n.name))
		var req := {
			"worker_id": worker_id,
			"name": str(worker.name),
			"role": str(worker.get("worker_role") if worker.get("worker_role") != null else "员工"),
			"personality": str(worker.get("worker_personality") if worker.get("worker_personality") != null else ""),
			"state": sm_state,
			"location": str(worker.get("last_marker") if worker.get("last_marker") != null else ""),
			"nearby_workers": nearby_names,
			"last_event": "",
			"current_task": str(worker.get("current_task_id") if worker.get("current_task_id") != null else ""),
			"energy": float(worker.get("worker_energy")) if worker.get("worker_energy") != null else 1.0,
			"stress": float(worker.get("worker_stress")) if worker.get("worker_stress") != null else 0.0,
		}
		worker_requests.append(req)

	if worker_requests.is_empty():
		return

	backend.send_json({
		"type": "atmosphere_request",
		"worker_id": "office",
		"payload": {
			"workers": worker_requests,
		},
	})
	if debug_panel != null:
		debug_panel.log_sent("ATMOSPHERE_REQUEST workers=%d" % worker_requests.size())


## 处理后端返回的 atmosphere_response：更新对应 worker 的氛围文本。
## 此方法由 _apply_agent_command 中 action == "atmosphere_response" 时调用。
func _apply_atmosphere_response(command: Dictionary) -> void:
	var payload_value: Variant = command.get("payload", {})
	if !(payload_value is Dictionary) and !(payload_value is Array):
		return

	var atmospheres: Dictionary = {}
	# 后端实际发送的是列表格式 [{worker_id, say, status, mood}, ...]
	if payload_value is Array:
		for item in payload_value as Array:
			if item is Dictionary:
				var wid := str((item as Dictionary).get("worker_id", ""))
				if not wid.is_empty():
					atmospheres[wid] = item
	else:
		# 兼容字典格式 {"workers": {worker_id: {...}}}
		var payload := payload_value as Dictionary
		var worker_atmospheres: Variant = payload.get("workers", {})
		if worker_atmospheres is Dictionary:
			atmospheres = worker_atmospheres as Dictionary

	for worker_id in atmospheres:
		var info: Variant = atmospheres[worker_id]
		if !(info is Dictionary):
			continue
		var atm := info as Dictionary
		var worker: Node = worker_nodes.get(worker_id) as Node
		if worker == null:
			continue

		# 更新氛围文本字段（只存状态，不冒气泡——坐在工位上自言自语像神经病）
		var say_text := str(atm.get("say", ""))
		var status_text := str(atm.get("status", ""))
		var mood_text := str(atm.get("mood", ""))
		if worker.get("atmosphere_say") != null:
			worker.set("atmosphere_say", say_text)
		if worker.get("atmosphere_status") != null:
			worker.set("atmosphere_status", status_text)
		if worker.get("atmosphere_mood") != null:
			worker.set("atmosphere_mood", mood_text)

		# 更新状态气泡
		if !status_text.is_empty():
			_update_status_bubble(worker_id)

	if debug_panel != null:
		debug_panel.log_received("ATMOSPONSE_RESPONSE updated=%d" % atmospheres.size())
