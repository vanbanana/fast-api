extends Node
## 顶部居中 token 消耗条：展示后端累计的 LLM token 用量（来自官方 usage 字段）。

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")

var theme: PixelUiTheme

var _styles: PixelStyles
var _label: Label


func setup(ui_theme: PixelUiTheme) -> void:
	theme = ui_theme
	_styles = PixelStyles.new(theme)

	var layer := CanvasLayer.new()
	layer.name = "TokenBarLayer"
	add_child(layer)

	var panel := PanelContainer.new()
	panel.name = "TokenBarPanel"
	panel.set_anchors_preset(Control.PRESET_CENTER_TOP)
	panel.custom_minimum_size = theme.token_bar_size
	panel.offset_left = -theme.token_bar_size.x / 2.0
	panel.offset_right = theme.token_bar_size.x / 2.0
	panel.offset_top = theme.token_bar_margin_top
	panel.offset_bottom = theme.token_bar_margin_top + theme.token_bar_size.y
	panel.grow_horizontal = Control.GROW_DIRECTION_BOTH
	panel.grow_vertical = Control.GROW_DIRECTION_END
	_styles.apply_panel_style(panel)
	layer.add_child(panel)

	_label = Label.new()
	_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_label.add_theme_font_size_override("font_size", theme.ui_font_size)
	_label.add_theme_color_override("font_color", theme.text_color)
	_label.text = "Token: 0"
	panel.add_child(_label)


func update_usage(usage: Dictionary) -> void:
	if _label == null:
		return
	var prompt_tokens := int(usage.get("prompt_tokens", 0))
	var completion_tokens := int(usage.get("completion_tokens", 0))
	var total_tokens := int(usage.get("total_tokens", 0))
	var calls := int(usage.get("calls", 0))
	_label.text = "Token 输入 %d | 输出 %d | 共 %d (%d 次调用)" % [
		prompt_tokens, completion_tokens, total_tokens, calls,
	]
