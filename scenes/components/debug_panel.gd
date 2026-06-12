extends Node
## F12 调试面板：展示 Godot <-> 后端 <-> 角色移动链路的实时状态。

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")
const AgentStateStore := preload("res://scenes/components/agent_state_store.gd")

var theme: PixelUiTheme
var store: AgentStateStore
var target_counts: Dictionary = {}

var _styles: PixelStyles
var _panel: PanelContainer
var _label: Label
var _visible: bool = false
var _sent_log: Array[String] = []
var _received_log: Array[String] = []
var _status_provider: Callable
var _worker_line_provider: Callable


func setup(ui_theme: PixelUiTheme, state_store: AgentStateStore, status_provider: Callable, worker_line_provider: Callable) -> void:
	theme = ui_theme
	store = state_store
	_status_provider = status_provider
	_worker_line_provider = worker_line_provider
	_styles = PixelStyles.new(theme)

	var layer := CanvasLayer.new()
	layer.name = "DebugLayer"
	layer.layer = 50
	add_child(layer)

	_panel = PanelContainer.new()
	_panel.name = "DebugPanel"
	_panel.visible = false
	_panel.position = Vector2(8, 92)
	_panel.custom_minimum_size = Vector2(620, 560)
	_panel.mouse_filter = Control.MOUSE_FILTER_PASS
	_styles.apply_panel_style(_panel)
	layer.add_child(_panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", theme.row_separation)
	_panel.add_child(box)

	var title := Label.new()
	title.text = "F12 Debug 链路面板"
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", theme.detail_text_color)
	box.add_child(title)

	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(596, 500)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(scroll)

	_label = Label.new()
	_label.custom_minimum_size = Vector2(572, 0)
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	_label.add_theme_color_override("font_color", theme.detail_text_color)
	scroll.add_child(_label)


func _process(_delta: float) -> void:
	if _visible:
		_refresh()


func toggle() -> void:
	_visible = !_visible
	if _panel != null:
		_panel.visible = _visible
	if _visible:
		_refresh()


func log_sent(text: String) -> void:
	_push_log(_sent_log, text)


func log_received(text: String) -> void:
	_push_log(_received_log, text)


func log_received_command(command: Dictionary) -> void:
	_push_log(_received_log, command_summary(command))


func command_summary(command: Dictionary) -> String:
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


func _refresh() -> void:
	if _label == null:
		return

	var lines: Array[String] = []
	lines.append("按 F12 关闭。这里显示 Godot <-> 后端 <-> 角色移动的实时状态。")
	if _status_provider.is_valid():
		lines.append(str(_status_provider.call()))
	lines.append("targets seat=%s idle=%s roam=%s total=%s" % [
		str(target_counts.get("seat", 0)),
		str(target_counts.get("idle", 0)),
		str(target_counts.get("roam", 0)),
		str(target_counts.get("total", 0)),
	])
	lines.append("")
	lines.append("Workers")
	if _worker_line_provider.is_valid():
		var worker_lines: Variant = _worker_line_provider.call()
		if worker_lines is Array:
			for line in worker_lines:
				lines.append(str(line))
	lines.append("")
	lines.append("Sent")
	lines.append(_format_log(_sent_log))
	lines.append("")
	lines.append("Received")
	lines.append(_format_log(_received_log))
	_label.text = "\n".join(lines)


func _push_log(log: Array[String], text: String) -> void:
	var compact_text := _shorten_text(text, 260)
	if compact_text.begins_with("stream_delta "):
		return
	if !log.is_empty() and log[-1] == compact_text:
		return
	log.append(compact_text)
	if log.size() > 24:
		log.remove_at(0)


func _format_log(log: Array[String]) -> String:
	if log.is_empty():
		return "- empty"
	var lines: Array[String] = []
	for item in log:
		lines.append("- %s" % item)
	return "\n".join(lines)


func _shorten_text(text: String, max_length: int) -> String:
	if text.length() <= max_length:
		return text
	return text.substr(0, max_length) + "..."
