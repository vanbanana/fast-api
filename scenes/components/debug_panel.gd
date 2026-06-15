extends Node
## F12 调试面板：精简版，只显示关键状态和重要事件。

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")
const AgentStateStore := preload("res://scenes/components/agent_state_store.gd")

var theme: PixelUiTheme
var store: AgentStateStore
var target_counts: Dictionary = {}

var _styles: PixelStyles
var _panel: PanelContainer
var _label: Label
var _visible: bool = false
var _status_provider: Callable
var _worker_line_provider: Callable

# 只记录重要事件（最多 12 条，LLM 日志单独计数）
var _events: Array[String] = []
var _llm_events: Array[String] = []


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
	_panel.custom_minimum_size = Vector2(520, 420)
	_panel.mouse_filter = Control.MOUSE_FILTER_PASS
	_styles.apply_panel_style(_panel)
	layer.add_child(_panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", theme.row_separation)
	_panel.add_child(box)

	var title := Label.new()
	title.text = "F12 Debug"
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", theme.detail_text_color)
	box.add_child(title)

	_label = Label.new()
	_label.custom_minimum_size = Vector2(490, 0)
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	_label.add_theme_color_override("font_color", theme.detail_text_color)
	box.add_child(_label)


func _process(_delta: float) -> void:
	if _visible:
		_refresh()


func toggle() -> void:
	_visible = !_visible
	if _panel != null:
		_panel.visible = _visible
	if _visible:
		_refresh()


## 只记录重要事件：老板指令、找人、会议、错误。
func log_sent(text: String) -> void:
	if text.begins_with("BOSS_INPUT"):
		_add_event("[→] %s" % text.substr(10))
	elif text.begins_with("ATMOSPHERE_REQUEST"):
		pass  # 氛围请求太频繁，忽略


func log_received(text: String) -> void:
	if text.begins_with("ERRAND_SEEK") or text.begins_with("ERRAND_SEEK_MISSING"):
		_add_event("[SEEK] %s" % text)
	elif text.begins_with("MOVE_FAILED") or text.begins_with("PARSE_FAILED"):
		_add_event("[!] %s" % text)
	elif text.begins_with("CHAT"):
		_add_event("[CHAT] %s" % text)


func log_received_command(command: Dictionary) -> void:
	var action := str(command.get("action", ""))
	var worker_id := str(command.get("worker_id", ""))
	var say := str(command.get("say", ""))
	if action == "errand_seek":
		_add_event("[SEEK] %s → 找人" % worker_id)
	elif action == "move_to":
		var target_id := str(command.get("target_id", ""))
		_add_event("[MOVE] %s → %s" % [worker_id, target_id])
	elif !say.is_empty() and action == "say":
		_add_event("[%s] %s: %s" % [action.to_upper(), worker_id, say.substr(0, 50)])
	elif action == "atmosphere_response":
		pass  # 氛围响应太频繁，忽略


func log_event(text: String) -> void:
	_add_event("[EVENT] %s" % text)


func log_llm_call(tag: String, tokens_in: int, tokens_out: int, result: String) -> void:
	_add_llm_event("🤖 %s | %d→%d tok | %s" % [tag, tokens_in, tokens_out, result.substr(0, 60)])


func _add_event(text: String) -> void:
	text = _shorten(text, 200)
	_events.append(text)
	if _events.size() > 12:
		_events.remove_at(0)


func _add_llm_event(text: String) -> void:
	text = _shorten(text, 200)
	_llm_events.append(text)
	# LLM 日志保留最近 5 条（太多会刷屏）
	if _llm_events.size() > 5:
		_llm_events.remove_at(0)


func _refresh() -> void:
	if _label == null:
		return

	var lines: Array[String] = []
	# 连接状态 + 目标计数
	if _status_provider.is_valid():
		lines.append(str(_status_provider.call()))
	lines.append("座位=%d 休息点=%d  漫游点=%d" % [
		target_counts.get("seat", 0),
		target_counts.get("idle", 0),
		target_counts.get("roam", 0),
	])
	lines.append("")

	# 员工状态（紧凑单行）
	if _worker_line_provider.is_valid():
		var worker_lines: Variant = _worker_line_provider.call()
		if worker_lines is Array:
			for line in worker_lines:
				lines.append(str(line))

	# 重要事件
	if !_events.is_empty():
		lines.append("")
		lines.append("--- 事件 ---")
		for ev in _events:
			lines.append(str(ev))

	# LLM 调用日志
	if !_llm_events.is_empty():
		lines.append("")
		lines.append("--- LLM ---")
		for ev in _llm_events:
			lines.append(str(ev))

	_label.text = "\n".join(lines)


func _shorten(text: String, max_len: int) -> String:
	if text.length() <= max_len:
		return text
	return text.substr(0, max_len) + "..."
