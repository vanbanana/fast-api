extends Node
## 老板指令输入栏 + 状态文案。

signal command_submitted(text: String)

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")

var theme: PixelUiTheme

var _styles: PixelStyles
var _command_input: LineEdit
var _status_label: Label


func setup(ui_theme: PixelUiTheme) -> void:
	theme = ui_theme
	_styles = PixelStyles.new(theme)

	var layer := CanvasLayer.new()
	layer.name = "BossCommandLayer"
	add_child(layer)

	var panel := PanelContainer.new()
	panel.name = "BossCommandPanel"
	panel.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	panel.custom_minimum_size = theme.boss_panel_size
	panel.offset_left = -theme.boss_panel_size.x / 2.0
	panel.offset_right = theme.boss_panel_size.x / 2.0
	panel.offset_bottom = -theme.boss_panel_margin_bottom
	panel.offset_top = -theme.boss_panel_margin_bottom - theme.boss_panel_size.y
	panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	panel.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_styles.apply_panel_style(panel)
	layer.add_child(panel)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", theme.row_separation)
	panel.add_child(box)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", theme.row_separation)
	box.add_child(row)

	_command_input = LineEdit.new()
	_command_input.placeholder_text = "输入老板指令"
	_command_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_command_input.text_submitted.connect(_on_text_submitted)
	_styles.apply_line_edit_style(_command_input)
	row.add_child(_command_input)

	var send_button := Button.new()
	send_button.text = "发送"
	send_button.pressed.connect(_submit)
	_styles.apply_button_style(send_button)
	row.add_child(send_button)

	_status_label = Label.new()
	_status_label.text = "等待后端连接"
	_status_label.add_theme_font_size_override("font_size", theme.ui_font_size)
	box.add_child(_status_label)


func set_status(text: String) -> void:
	if _status_label != null:
		_status_label.text = text


func status_text() -> String:
	if _status_label != null:
		return _status_label.text
	return ""


func _on_text_submitted(_text: String) -> void:
	_submit()


func _submit() -> void:
	if _command_input == null:
		return

	var text := _command_input.text.strip_edges()
	if text.is_empty():
		return
	command_submitted.emit(text)
	_command_input.clear()
