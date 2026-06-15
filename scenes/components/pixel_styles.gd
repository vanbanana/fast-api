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
	var style := StyleBoxEmpty.new()
	# Set margins to hold text away from the 4px border regions
	style.set_content_margin(SIDE_LEFT, 8.0)
	style.set_content_margin(SIDE_TOP, 6.0)
	style.set_content_margin(SIDE_RIGHT, 8.0)
	style.set_content_margin(SIDE_BOTTOM, 6.0)
	panel.add_theme_stylebox_override("panel", style)
	
	if not panel.is_connected("draw", _draw_speech_bubble.bind(panel)):
		panel.connect("draw", _draw_speech_bubble.bind(panel))


func apply_status_bubble_style(panel: PanelContainer) -> void:
	var style := StyleBoxEmpty.new()
	style.set_content_margin(SIDE_LEFT, 6.0)
	style.set_content_margin(SIDE_TOP, 4.0)
	style.set_content_margin(SIDE_RIGHT, 6.0)
	style.set_content_margin(SIDE_BOTTOM, 4.0)
	panel.add_theme_stylebox_override("panel", style)
	
	if not panel.is_connected("draw", _draw_status_bubble.bind(panel)):
		panel.connect("draw", _draw_status_bubble.bind(panel))


func draw_pixel_box_with_cut_corners(canvas: CanvasItem, rect: Rect2, color: Color) -> void:
	var x := rect.position.x
	var y := rect.position.y
	var w := rect.size.x
	var h := rect.size.y
	if w <= 2.0 or h <= 2.0:
		canvas.draw_rect(rect, color)
		return
	# Draw middle horizontal part
	canvas.draw_rect(Rect2(x, y + 1.0, w, h - 2.0), color)
	# Draw top row (inset by 1)
	canvas.draw_rect(Rect2(x + 1.0, y, w - 2.0, 1.0), color)
	# Draw bottom row (inset by 1)
	canvas.draw_rect(Rect2(x + 1.0, y + h - 1.0, w - 2.0, 1.0), color)


func _draw_speech_bubble(panel: PanelContainer) -> void:
	var size := panel.size
	var bg_color := theme.speech_bg_color
	var border_color := theme.speech_border_color
	var inner_border_color := Color(1.0, 1.0, 1.0, 1.0) # White highlight
	var shadow_color := Color(0.0, 0.0, 0.0, 0.22)
	
	var cx := int(size.x / 2)
	var h := size.y
	
	# --- 1. Draw Shadow ---
	var shadow_rect := Rect2(2.0, 2.0, size.x, size.y)
	draw_pixel_box_with_cut_corners(panel, shadow_rect, shadow_color)
	panel.draw_rect(Rect2(cx - 5 + 2, h + 2, 10, 1), shadow_color)
	panel.draw_rect(Rect2(cx - 4 + 2, h + 1 + 2, 8, 1), shadow_color)
	panel.draw_rect(Rect2(cx - 3 + 2, h + 2 + 2, 6, 1), shadow_color)
	panel.draw_rect(Rect2(cx - 2 + 2, h + 3 + 2, 4, 1), shadow_color)
	panel.draw_rect(Rect2(cx - 1 + 2, h + 4 + 2, 2, 1), shadow_color)
	
	# --- 2. Draw Outer Border ---
	draw_pixel_box_with_cut_corners(panel, Rect2(0.0, 0.0, size.x, size.y), border_color)
	panel.draw_rect(Rect2(cx - 5, h, 10, 1), border_color)
	panel.draw_rect(Rect2(cx - 4, h + 1, 8, 1), border_color)
	panel.draw_rect(Rect2(cx - 3, h + 2, 6, 1), border_color)
	panel.draw_rect(Rect2(cx - 2, h + 3, 4, 1), border_color)
	panel.draw_rect(Rect2(cx - 1, h + 4, 2, 1), border_color)
	
	# --- 3. Draw Inner Highlight Border ---
	draw_pixel_box_with_cut_corners(panel, Rect2(1.0, 1.0, size.x - 2.0, size.y - 2.0), inner_border_color)
	panel.draw_rect(Rect2(cx - 4, h - 1, 8, 1), inner_border_color)
	panel.draw_rect(Rect2(cx - 3, h, 6, 1), inner_border_color)
	panel.draw_rect(Rect2(cx - 2, h + 1, 4, 1), inner_border_color)
	panel.draw_rect(Rect2(cx - 1, h + 2, 2, 1), inner_border_color)
	
	# --- 4. Draw Background Fill ---
	draw_pixel_box_with_cut_corners(panel, Rect2(2.0, 2.0, size.x - 4.0, size.y - 4.0), bg_color)
	panel.draw_rect(Rect2(cx - 3, h - 2, 6, 1), bg_color)
	panel.draw_rect(Rect2(cx - 3, h - 1, 6, 1), bg_color)
	panel.draw_rect(Rect2(cx - 2, h, 4, 1), bg_color)
	panel.draw_rect(Rect2(cx - 1, h + 1, 2, 1), bg_color)


func _draw_status_bubble(panel: PanelContainer) -> void:
	var size := panel.size
	var bg_color: Color = panel.get_meta("bg_color", theme.status_bg_color)
	var dark_border := Color(0.12, 0.11, 0.10)
	var shadow_color := Color(0.0, 0.0, 0.0, 0.2)
	
	# Draw shadow (1px offset)
	draw_pixel_box_with_cut_corners(panel, Rect2(1.0, 1.0, size.x, size.y), shadow_color)
	
	# Draw outer border
	draw_pixel_box_with_cut_corners(panel, Rect2(0.0, 0.0, size.x, size.y), dark_border)
	
	# Draw background
	draw_pixel_box_with_cut_corners(panel, Rect2(1.0, 1.0, size.x - 2.0, size.y - 2.0), bg_color)


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
