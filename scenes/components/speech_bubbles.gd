extends Node
## 角色头顶气泡：常驻状态小气泡 + 思考点点点 + 打字机台词，完全独立于业务逻辑。
## 对话气泡限定最大宽高，流式文本在固定区域内自动滚动。

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")

var theme: PixelUiTheme
var _styles: PixelStyles

var _panels: Dictionary = {}
var _labels: Dictionary = {}
var _scrolls: Dictionary = {}
var _status_labels: Dictionary = {}
var _full_text: Dictionary = {}
var _visible_chars: Dictionary = {}
var _modes: Dictionary = {}
var _hold_timers: Dictionary = {}
var _dot_timers: Dictionary = {}


func setup(ui_theme: PixelUiTheme, worker_nodes: Dictionary) -> void:
	theme = ui_theme
	_styles = PixelStyles.new(theme)
	for worker_id in worker_nodes.keys():
		var worker := worker_nodes[worker_id] as Node2D
		if worker == null:
			continue

		var panel := PanelContainer.new()
		panel.name = "SpeechBubble"
		panel.visible = false
		panel.z_index = 200
		panel.z_as_relative = false
		panel.position = theme.speech_bubble_offset
		panel.custom_minimum_size = theme.speech_bubble_size
		panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_styles.apply_speech_bubble_style(panel)
		worker.add_child(panel)

		var scroll := ScrollContainer.new()
		scroll.custom_minimum_size = Vector2(theme.speech_bubble_size.x - 8.0, theme.speech_bubble_max_height)
		scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
		scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
		scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_SHOW_NEVER
		scroll.mouse_filter = Control.MOUSE_FILTER_IGNORE
		panel.add_child(scroll)

		var label := Label.new()
		label.custom_minimum_size = Vector2(theme.speech_bubble_size.x - 8.0, 0)
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.add_theme_font_size_override("font_size", theme.speech_font_size)
		label.add_theme_color_override("font_color", theme.speech_text_color)
		label.mouse_filter = Control.MOUSE_FILTER_IGNORE
		scroll.add_child(label)

		var status_panel := PanelContainer.new()
		status_panel.name = "StatusBubble"
		status_panel.visible = false
		status_panel.z_index = 199
		status_panel.z_as_relative = false
		status_panel.position = theme.status_bubble_offset
		status_panel.custom_minimum_size = theme.status_bubble_min_size
		status_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_styles.apply_status_bubble_style(status_panel)
		worker.add_child(status_panel)

		var status_label := Label.new()
		status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		status_label.add_theme_font_size_override("font_size", theme.status_font_size)
		status_label.add_theme_color_override("font_color", theme.status_text_color)
		status_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
		status_panel.add_child(status_label)

		_panels[worker_id] = panel
		_labels[worker_id] = label
		_scrolls[worker_id] = scroll
		_status_labels[worker_id] = status_label
		_full_text[worker_id] = ""
		_visible_chars[worker_id] = 0.0
		_modes[worker_id] = "hidden"
		_hold_timers[worker_id] = 0.0
		_dot_timers[worker_id] = 0.0


func _process(delta: float) -> void:
	for worker_id in _panels.keys():
		var panel := _panels[worker_id] as PanelContainer
		var label := _labels[worker_id] as Label
		if panel == null or label == null:
			continue

		var mode := str(_modes.get(worker_id, "hidden"))
		if mode == "hidden":
			panel.visible = false
		elif mode == "thinking":
			panel.visible = true
			var dot_timer := float(_dot_timers.get(worker_id, 0.0)) + delta
			if dot_timer >= theme.thinking_dot_seconds:
				dot_timer = 0.0
				var dot_count := (label.text.length() % 3) + 1
				label.text = ".".repeat(dot_count)
			_dot_timers[worker_id] = dot_timer
		elif mode == "speaking":
			panel.visible = true
			var full_text := str(_full_text.get(worker_id, ""))
			var visible_count := float(_visible_chars.get(worker_id, 0.0))
			if visible_count < float(full_text.length()):
				visible_count = minf(float(full_text.length()), visible_count + theme.speech_type_chars_per_second * delta)
				_visible_chars[worker_id] = visible_count
				label.text = full_text.substr(0, int(visible_count))
				_scroll_to_bottom(worker_id)
			else:
				label.text = full_text
				_scroll_to_bottom(worker_id)
				var hold_time := float(_hold_timers.get(worker_id, 0.0)) + delta
				_hold_timers[worker_id] = hold_time
				if hold_time >= theme.speech_hold_seconds:
					_modes[worker_id] = "hidden"


func show_thinking(worker_id: String) -> void:
	if worker_id.is_empty() or !_panels.has(worker_id):
		return
	var panel := _panels[worker_id] as PanelContainer
	var label := _labels[worker_id] as Label
	if panel == null or label == null:
		return
	panel.visible = true
	label.text = "."
	_modes[worker_id] = "thinking"
	_hold_timers[worker_id] = 0.0
	_dot_timers[worker_id] = 0.0


func show_speech(worker_id: String, text: String) -> void:
	if worker_id.is_empty() or !_panels.has(worker_id):
		return
	text = text.strip_edges()
	if text.is_empty():
		return
	var panel := _panels[worker_id] as PanelContainer
	var label := _labels[worker_id] as Label
	if panel == null or label == null:
		return
	panel.visible = true
	label.text = ""
	_full_text[worker_id] = text
	_visible_chars[worker_id] = 0.0
	_modes[worker_id] = "speaking"
	_hold_timers[worker_id] = 0.0


func clear_all() -> void:
	for worker_id in _panels.keys():
		_modes[worker_id] = "hidden"
		_full_text[worker_id] = ""
		_visible_chars[worker_id] = 0.0
		_hold_timers[worker_id] = 0.0
		var panel := _panels[worker_id] as PanelContainer
		if panel != null:
			panel.visible = false


func mode_for(worker_id: String) -> String:
	return str(_modes.get(worker_id, "hidden"))


func set_status(worker_id: String, text: String) -> void:
	var status_label := _status_labels.get(worker_id) as Label
	if status_label == null:
		return
	text = text.strip_edges()
	var status_panel := status_label.get_parent() as PanelContainer
	if status_panel == null:
		return
	if text.is_empty():
		status_panel.visible = false
		return
	status_label.text = text.substr(0, 14)
	status_panel.visible = true


func _scroll_to_bottom(worker_id: String) -> void:
	var scroll := _scrolls.get(worker_id) as ScrollContainer
	if scroll == null:
		return
	var bar := scroll.get_v_scroll_bar()
	if bar != null:
		scroll.scroll_vertical = int(bar.max_value)
