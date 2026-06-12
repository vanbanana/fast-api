extends RefCounted
## 程序化像素 UI 样式工具：所有面板/按钮/输入框共用同一套主题样式。

var theme: PixelUiTheme


func _init(ui_theme: PixelUiTheme) -> void:
	theme = ui_theme


func apply_panel_style(panel: PanelContainer) -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = theme.panel_bg_color
	style.border_color = theme.panel_border_color
	style.set_border_width_all(theme.panel_border_width)
	style.set_corner_radius_all(theme.corner_radius)
	style.set_content_margin(SIDE_LEFT, theme.panel_margin_left)
	style.set_content_margin(SIDE_TOP, theme.panel_margin_top)
	style.set_content_margin(SIDE_RIGHT, theme.panel_margin_right)
	style.set_content_margin(SIDE_BOTTOM, theme.panel_margin_bottom)
	panel.add_theme_stylebox_override("panel", style)


func apply_speech_bubble_style(panel: PanelContainer) -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = theme.speech_bg_color
	style.border_color = theme.speech_border_color
	style.set_border_width_all(theme.speech_border_width)
	style.set_corner_radius_all(theme.speech_corner_radius)
	style.set_content_margin(SIDE_LEFT, 4.0)
	style.set_content_margin(SIDE_TOP, 3.0)
	style.set_content_margin(SIDE_RIGHT, 4.0)
	style.set_content_margin(SIDE_BOTTOM, 3.0)
	panel.add_theme_stylebox_override("panel", style)


func apply_status_bubble_style(panel: PanelContainer) -> void:
	var style := StyleBoxFlat.new()
	style.bg_color = theme.status_bg_color
	style.border_color = theme.status_border_color
	style.set_border_width_all(theme.status_border_width)
	style.set_corner_radius_all(theme.status_corner_radius)
	style.set_content_margin(SIDE_LEFT, 3.0)
	style.set_content_margin(SIDE_TOP, 1.0)
	style.set_content_margin(SIDE_RIGHT, 3.0)
	style.set_content_margin(SIDE_BOTTOM, 1.0)
	panel.add_theme_stylebox_override("panel", style)


func apply_line_edit_style(line_edit: LineEdit) -> void:
	line_edit.add_theme_font_size_override("font_size", theme.ui_font_size)
	line_edit.add_theme_color_override("font_color", theme.text_color)
	line_edit.add_theme_color_override("font_placeholder_color", theme.placeholder_color)
	var normal := make_pixel_box(theme.field_bg_color, theme.field_border_color, theme.field_border_width)
	var focus := make_pixel_box(theme.field_focus_bg_color, theme.field_focus_border_color, theme.field_focus_border_width)
	line_edit.add_theme_stylebox_override("normal", normal)
	line_edit.add_theme_stylebox_override("focus", focus)


func apply_button_style(button: Button) -> void:
	button.add_theme_font_size_override("font_size", theme.ui_font_size)
	button.add_theme_color_override("font_color", theme.text_color)
	button.add_theme_stylebox_override("normal", make_pixel_box(theme.button_bg_color, theme.button_border_color, theme.field_border_width))
	button.add_theme_stylebox_override("hover", make_pixel_box(theme.button_hover_bg_color, theme.button_active_border_color, theme.field_border_width))
	button.add_theme_stylebox_override("pressed", make_pixel_box(theme.button_pressed_bg_color, theme.button_active_border_color, theme.field_focus_border_width))


func make_pixel_box(bg_color: Color, border_color: Color, border_width: int) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = bg_color
	style.border_color = border_color
	style.set_border_width_all(border_width)
	style.set_corner_radius_all(theme.corner_radius)
	style.set_content_margin(SIDE_LEFT, theme.content_margin_left)
	style.set_content_margin(SIDE_TOP, theme.content_margin_top)
	style.set_content_margin(SIDE_RIGHT, theme.content_margin_right)
	style.set_content_margin(SIDE_BOTTOM, theme.content_margin_bottom)
	return style
