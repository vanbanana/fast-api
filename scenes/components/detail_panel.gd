extends Node
## 员工悬停/点击详情面板：档案、思考流、任务、记忆四个页签。

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")
const AgentStateStore := preload("res://scenes/components/agent_state_store.gd")

var theme: PixelUiTheme
var store: AgentStateStore
var worker_group: StringName = &"demo_workers"
var worker_hover_radius: float = 14.0
var backend_agent_prompts_dir: String = "backend/prompts/agents"
var local_worker_profiles: Dictionary = {}

var _styles: PixelStyles
var _scene_root: Node2D
var _panel: PanelContainer
var _label: Label
var _tab_buttons: Dictionary = {}
var _active_tab: String = "profile"
var _hovered_worker: Node2D
var _focused_worker: Node2D
var _pinned_worker: Node2D


func setup(scene_root: Node2D, ui_theme: PixelUiTheme, state_store: AgentStateStore) -> void:
	theme = ui_theme
	store = state_store
	_scene_root = scene_root
	_styles = PixelStyles.new(theme)

	var layer := CanvasLayer.new()
	layer.name = "WorkerHoverLayer"
	add_child(layer)

	_panel = PanelContainer.new()
	_panel.name = "WorkerDetailPanel"
	_panel.visible = false
	_panel.custom_minimum_size = theme.detail_panel_size
	_styles.apply_panel_style(_panel)
	layer.add_child(_panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", theme.row_separation)
	_panel.add_child(box)

	var tabs := HBoxContainer.new()
	tabs.add_theme_constant_override("separation", theme.row_separation)
	box.add_child(tabs)
	_add_tab_button(tabs, "profile", "档案")
	_add_tab_button(tabs, "stream", "思考")
	_add_tab_button(tabs, "task", "任务")
	_add_tab_button(tabs, "memory", "记忆")

	_label = Label.new()
	_label.custom_minimum_size = Vector2(theme.detail_panel_size.x - theme.panel_margin_left - theme.panel_margin_right, 0)
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	_label.add_theme_color_override("font_color", theme.detail_text_color)
	box.add_child(_label)
	_refresh_tab_buttons()


func _process(_delta: float) -> void:
	if _panel == null or _scene_root == null:
		return

	var over_panel := is_mouse_over_panel()
	_hovered_worker = _find_hovered_worker()
	if _pinned_worker != null:
		_focused_worker = _pinned_worker
	elif over_panel and _focused_worker != null:
		pass
	elif _hovered_worker != null:
		_focused_worker = _hovered_worker
	else:
		_focused_worker = null

	if _focused_worker == null:
		_panel.visible = false
		return

	var worker_id := str(_focused_worker.name)
	_label.text = _build_worker_detail_text(worker_id)
	if _pinned_worker == null and _hovered_worker != null:
		_position_panel()
	_panel.visible = true


func handle_click() -> void:
	## 点击员工固定面板，再点一次取消固定。
	if is_mouse_over_panel():
		return
	var clicked_worker := _find_hovered_worker()
	if clicked_worker == null:
		_pinned_worker = null
		return
	if _pinned_worker == clicked_worker:
		_pinned_worker = null
	else:
		_pinned_worker = clicked_worker
		_focused_worker = clicked_worker


func is_mouse_over_panel() -> bool:
	if _panel == null or !_panel.visible:
		return false
	var mouse_position := _panel.get_viewport().get_mouse_position()
	var panel_rect := Rect2(_panel.global_position, _panel.size)
	if panel_rect.size.x <= 1.0 or panel_rect.size.y <= 1.0:
		panel_rect.size = _panel.custom_minimum_size
	return panel_rect.has_point(mouse_position)


func _add_tab_button(parent: HBoxContainer, tab_id: String, title: String) -> void:
	var button := Button.new()
	button.text = title
	button.toggle_mode = true
	button.pressed.connect(_set_tab.bind(tab_id))
	_styles.apply_button_style(button)
	parent.add_child(button)
	_tab_buttons[tab_id] = button


func _set_tab(tab_id: String) -> void:
	_active_tab = tab_id
	_refresh_tab_buttons()


func _refresh_tab_buttons() -> void:
	for tab_id in _tab_buttons.keys():
		var button := _tab_buttons[tab_id] as Button
		if button == null:
			continue
		button.button_pressed = tab_id == _active_tab


func _find_hovered_worker() -> Node2D:
	var mouse_position := _scene_root.get_global_mouse_position()
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


func _position_panel() -> void:
	var viewport := _panel.get_viewport()
	var mouse_screen_position := viewport.get_mouse_position()
	var viewport_size := viewport.get_visible_rect().size
	var panel_size := _panel.size
	if panel_size.x <= 1.0:
		panel_size = _panel.custom_minimum_size

	var panel_position := mouse_screen_position + theme.detail_panel_offset
	if panel_position.x + panel_size.x > viewport_size.x:
		panel_position.x = mouse_screen_position.x - panel_size.x - theme.detail_panel_offset.x
	if panel_position.y + panel_size.y > viewport_size.y:
		panel_position.y = viewport_size.y - panel_size.y - theme.viewport_padding
	panel_position.x = maxf(theme.viewport_padding, panel_position.x)
	panel_position.y = maxf(theme.viewport_padding, panel_position.y)
	_panel.position = panel_position


func _build_worker_detail_text(worker_id: String) -> String:
	var snapshot := store.snapshot_for(worker_id)
	if snapshot.is_empty():
		return _build_local_profile_text(worker_id)

	var context := store.context_for(worker_id)
	var lines: Array[String] = []
	if _active_tab == "profile":
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
	elif _active_tab == "stream":
		lines.append("%s 的现场思考流" % str(snapshot.get("name", worker_id)))
		lines.append(_format_stream_lines(worker_id))
	elif _active_tab == "task":
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
	elif _active_tab == "memory":
		lines.append("短期记忆")
		var memory_text := _format_recent_memory(snapshot.get("memory", []))
		lines.append(memory_text if !memory_text.is_empty() else "暂无新的工作记忆。")

	var last_say := str(snapshot.get("last_say", ""))
	if _active_tab == "stream" and !last_say.is_empty():
		lines.append("台词: %s" % last_say)
	return "\n".join(lines)


func _build_local_profile_text(worker_id: String) -> String:
	var profile: Dictionary = local_worker_profiles.get(worker_id, {"name": worker_id, "role": "员工"})
	return "%s  %s\n等待后端画像\n\n后端连接后会显示完整人设、任务、风险、协作和记忆。\n提示词目录: %s" % [
		str(profile.get("name", worker_id)),
		str(profile.get("role", "员工")),
		backend_agent_prompts_dir,
	]


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
	var stream := store.stream_for(worker_id)
	if stream.is_empty():
		return "正在观察办公室，还没有新的思考流。"

	var start_index := maxi(0, stream.size() - 8)
	var lines: Array[String] = []
	for index in range(start_index, stream.size()):
		lines.append("> %s" % str(stream[index]))
	return "\n".join(lines)
