extends Node
## 场景右侧的像素仿真手机：主屏可点开微信，微信里是公司工作群。
## 老板输入迁移到群聊输入框，支持 @某人（带选人 UI），员工的发言会同步进群。

signal command_submitted(text: String, target_worker_ids: Array[String])

const PixelStyles := preload("res://scenes/components/pixel_styles.gd")

const PHONE_SIZE := Vector2(238, 392)
const PHONE_MARGIN_RIGHT := 10.0
const SCREEN_BG := Color(0.09, 0.10, 0.12, 0.98)
const WECHAT_GREEN := Color(0.16, 0.68, 0.30, 1.0)
const BOSS_BUBBLE := Color(0.58, 0.88, 0.45, 1.0)
const WORKER_BUBBLE := Color(0.98, 0.98, 0.98, 1.0)
const BUBBLE_TEXT := Color(0.10, 0.10, 0.10, 1.0)
const MENTION_COLOR := Color(0.30, 0.55, 0.95, 1.0)

var theme: PixelUiTheme

var _styles: PixelStyles
var _worker_profiles: Dictionary = {}
var _home_screen: Control
var _chat_screen: Control
var _message_list: VBoxContainer
var _message_scroll: ScrollContainer
var _chat_input: LineEdit
var _mention_popup: PanelContainer
var _status_label: Label
var _clock_label: Label
var _mention_ids: Array[String] = []

var _send_button: Button
var _time_label: Label

# Camera and Album App Variables
var _phone_layer: CanvasLayer
var _camera_screen: Control
var _sub_viewport: SubViewport
var _sub_camera: Camera2D
var _shutter_preview_rect: TextureRect
var _flash_rect: ColorRect
var _gallery_screen: Control
var _gallery_grid: GridContainer
var _photo_preview_screen: Control
var _preview_texture_rect: TextureRect
var _current_photo_path: String = ""

# Task Screen Variables
var _task_screen: Control
var _task_list: VBoxContainer
var _task_scroll: ScrollContainer
var _task_data: Array = []  # 从后端同步过来的任务列表（Array[Dictionary]）
var _prd_summary: String = ""  # 会议PRD总结
var _meeting_topic: String = ""  # 会议主题


func setup(ui_theme: PixelUiTheme, worker_profiles: Dictionary) -> void:
	theme = ui_theme
	_styles = PixelStyles.new(theme)
	_worker_profiles = worker_profiles

	_phone_layer = CanvasLayer.new()
	_phone_layer.name = "PhoneLayer"
	add_child(_phone_layer)

	var phone := PanelContainer.new()
	phone.name = "PhonePanel"
	phone.set_anchors_preset(Control.PRESET_CENTER_RIGHT)
	phone.custom_minimum_size = PHONE_SIZE
	phone.offset_right = -PHONE_MARGIN_RIGHT
	phone.offset_left = -PHONE_MARGIN_RIGHT - PHONE_SIZE.x
	phone.offset_top = -PHONE_SIZE.y / 2.0
	phone.offset_bottom = PHONE_SIZE.y / 2.0
	phone.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	phone.grow_vertical = Control.GROW_DIRECTION_BOTH
	
	# Empty StyleBox to let us draw the physical phone shell procedurally
	var shell_style := StyleBoxEmpty.new()
	shell_style.set_content_margin(SIDE_LEFT, 8.0)
	shell_style.set_content_margin(SIDE_TOP, 24.0)
	shell_style.set_content_margin(SIDE_RIGHT, 8.0)
	shell_style.set_content_margin(SIDE_BOTTOM, 24.0)
	phone.add_theme_stylebox_override("panel", shell_style)
	
	if not phone.is_connected("draw", _draw_phone_shell.bind(phone)):
		phone.connect("draw", _draw_phone_shell.bind(phone))
	_phone_layer.add_child(phone)

	var screen := PanelContainer.new()
	screen.name = "PhoneScreen"
	screen.clip_contents = true
	var screen_style := StyleBoxFlat.new()
	screen_style.bg_color = SCREEN_BG
	screen_style.border_color = Color(0.02, 0.02, 0.02)
	screen_style.set_border_width_all(1)
	screen_style.set_corner_radius_all(0) # Sharp pixel corners
	screen.add_theme_stylebox_override("panel", screen_style)
	phone.add_child(screen)

	# Build Top Status Bar
	var status_bar := Control.new()
	status_bar.name = "StatusBar"
	status_bar.custom_minimum_size = Vector2(0, 14)
	status_bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	status_bar.set_anchors_preset(Control.PRESET_TOP_WIDE)
	status_bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if not status_bar.is_connected("draw", _draw_status_bar.bind(status_bar)):
		status_bar.connect("draw", _draw_status_bar.bind(status_bar))
	screen.add_child(status_bar)
	
	_time_label = Label.new()
	_time_label.name = "TimeLabel"
	_time_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_time_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_time_label.set_anchors_preset(Control.PRESET_FULL_RECT)
	_time_label.add_theme_font_size_override("font_size", 7)
	_time_label.add_theme_color_override("font_color", Color(0.9, 0.9, 0.9))
	status_bar.add_child(_time_label)

	_home_screen = _build_home_screen()
	screen.add_child(_home_screen)
	
	_chat_screen = _build_chat_screen()
	_chat_screen.visible = false
	screen.add_child(_chat_screen)
	
	_camera_screen = _build_camera_screen()
	_camera_screen.visible = false
	screen.add_child(_camera_screen)
	
	_gallery_screen = _build_gallery_screen()
	_gallery_screen.visible = false
	screen.add_child(_gallery_screen)
	
	_photo_preview_screen = _build_photo_preview_screen()
	_photo_preview_screen.visible = false
	screen.add_child(_photo_preview_screen)

	_task_screen = _build_task_screen()
	_task_screen.visible = false
	screen.add_child(_task_screen)

	_mention_popup = _build_mention_popup()
	_mention_popup.visible = false
	screen.add_child(_mention_popup)


func _process(_delta: float) -> void:
	if _time_label != null:
		var time_dict := Time.get_time_dict_from_system()
		_time_label.text = "%02d:%02d" % [time_dict.hour, time_dict.minute]
		
	# Synchronize camera if camera app is active
	if _camera_screen != null and _camera_screen.visible and _sub_camera != null:
		var main_cam := get_viewport().get_camera_2d()
		if main_cam == null:
			var cameras = get_tree().get_nodes_in_group("cameras")
			if not cameras.is_empty():
				main_cam = cameras[0]
			else:
				var root = get_tree().current_scene
				if root != null:
					main_cam = root.find_child("Camera2D", true, false)
		if main_cam != null:
			_sub_camera.global_position = main_cam.global_position - Vector2(150, 0)


func _draw_phone_shell(panel: PanelContainer) -> void:
	var size := panel.size
	var body_color := Color(0.12, 0.13, 0.15)
	var border_color := Color(0.02, 0.02, 0.02)
	var highlight_color := Color(0.35, 0.36, 0.38)
	
	# Draw physical shadow (offset to bottom-right by 3px)
	var shadow_color := Color(0.0, 0.0, 0.0, 0.25)
	_styles.draw_pixel_box_with_cut_corners(panel, Rect2(3.0, 3.0, size.x, size.y), shadow_color)
	
	# Draw main phone body (casing border)
	_styles.draw_pixel_box_with_cut_corners(panel, Rect2(0.0, 0.0, size.x, size.y), border_color)
	
	# Draw main phone body (casing fill, inset by 1px)
	_styles.draw_pixel_box_with_cut_corners(panel, Rect2(1.0, 1.0, size.x - 2.0, size.y - 2.0), body_color)
	
	# Draw inner screen bezel/outline
	panel.draw_rect(Rect2(7.0, 23.0, size.x - 14.0, size.y - 46.0), border_color)
	panel.draw_rect(Rect2(6.0, 22.0, size.x - 12.0, size.y - 44.0), highlight_color, false, 1.0)
	
	# Draw top details: speaker grille and camera notch
	var cx := int(size.x / 2)
	# Speaker grille (black bar at top)
	panel.draw_rect(Rect2(cx - 20, 10, 40, 3), border_color)
	panel.draw_rect(Rect2(cx - 19, 11, 38, 1), highlight_color)
	# Camera notch (small circular lens)
	panel.draw_rect(Rect2(cx - 32, 10, 3, 3), border_color)
	panel.draw_rect(Rect2(cx - 31, 11, 1, 1), Color(0.1, 0.3, 0.6))
	
	# Draw bottom details: circular home button
	var hb_y := size.y - 12
	panel.draw_rect(Rect2(cx - 5, hb_y - 5, 10, 10), border_color)
	panel.draw_rect(Rect2(cx - 4, hb_y - 4, 8, 8), highlight_color)
	panel.draw_rect(Rect2(cx - 3, hb_y - 3, 6, 6), body_color)


func _draw_status_bar(bar: Control) -> void:
	var size := bar.size
	var text_color := Color(0.9, 0.9, 0.9)
	var border_color := Color(0.12, 0.13, 0.15)
	
	# Background fill: translucent dark grey
	bar.draw_rect(Rect2(0, 0, size.x, size.y), Color(0.06, 0.07, 0.09, 0.92))
	# Bottom separator line
	bar.draw_rect(Rect2(0, size.y - 1, size.x, 1), Color(0.15, 0.16, 0.18))
	
	# Left side: Wi-Fi or Signal Icon
	var sig_x := 6.0
	var sig_y := 10.0
	bar.draw_rect(Rect2(sig_x, sig_y - 2, 1, 2), text_color)
	bar.draw_rect(Rect2(sig_x + 2, sig_y - 4, 1, 4), text_color)
	bar.draw_rect(Rect2(sig_x + 4, sig_y - 6, 1, 6), text_color)
	bar.draw_rect(Rect2(sig_x + 6, sig_y - 8, 1, 8), text_color)
	
	# Wi-Fi icon (3 small pixel curves)
	var wifi_x := 18.0
	var wifi_y := 5.0
	bar.draw_rect(Rect2(wifi_x + 2, wifi_y + 4, 1, 1), text_color)
	bar.draw_rect(Rect2(wifi_x + 1, wifi_y + 2, 3, 1), text_color)
	bar.draw_rect(Rect2(wifi_x, wifi_y, 5, 1), text_color)
	
	# Right side: Battery Icon
	var bat_x := size.x - 18.0
	var bat_y := 4.0
	# Outer border
	bar.draw_rect(Rect2(bat_x, bat_y, 11, 6), text_color, false, 1.0)
	# Tip
	bar.draw_rect(Rect2(bat_x + 11, bat_y + 2, 1, 2), text_color)
	# Charge level: 2 green bars
	bar.draw_rect(Rect2(bat_x + 2, bat_y + 2, 3, 2), Color(0.2, 0.9, 0.3))
	bar.draw_rect(Rect2(bat_x + 6, bat_y + 2, 3, 2), Color(0.2, 0.9, 0.3))


func _build_home_screen() -> Control:
	var margin_container := MarginContainer.new()
	margin_container.name = "HomeScreen"
	margin_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	margin_container.add_theme_constant_override("margin_left", 12)
	margin_container.add_theme_constant_override("margin_right", 12)
	margin_container.add_theme_constant_override("margin_top", 22) # leaves space for status bar
	margin_container.add_theme_constant_override("margin_bottom", 12)
	
	if not margin_container.is_connected("draw", _draw_home_screen.bind(margin_container)):
		margin_container.connect("draw", _draw_home_screen.bind(margin_container))
	
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 16)
	margin_container.add_child(box)
	
	# Title label (desktop header)
	var title := Label.new()
	title.text = "WECHAT OS"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size", 9)
	title.add_theme_color_override("font_color", Color(1.0, 1.0, 1.0, 0.6))
	title.add_theme_color_override("font_outline_color", Color.BLACK)
	title.add_theme_constant_override("outline_size", 2)
	box.add_child(title)
	
	var grid := GridContainer.new()
	grid.columns = 3
	grid.add_theme_constant_override("h_separation", 12)
	grid.add_theme_constant_override("v_separation", 12)
	grid.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	box.add_child(grid)
	
	# 1. WeChat Icon Button
	var wechat_btn := Button.new()
	wechat_btn.custom_minimum_size = Vector2(48, 48)
	wechat_btn.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	wechat_btn.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	wechat_btn.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	wechat_btn.pressed.connect(_open_chat)
	
	var wechat_box := VBoxContainer.new()
	wechat_box.add_theme_constant_override("separation", 2)
	wechat_box.set_anchors_preset(Control.PRESET_FULL_RECT)
	wechat_box.mouse_filter = Control.MOUSE_FILTER_IGNORE # Fix click detection
	wechat_btn.add_child(wechat_box)
	
	var wechat_logo := Control.new()
	wechat_logo.custom_minimum_size = Vector2(32, 32)
	wechat_logo.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	wechat_logo.mouse_filter = Control.MOUSE_FILTER_IGNORE
	wechat_logo.connect("draw", _draw_wechat_logo.bind(wechat_logo))
	wechat_box.add_child(wechat_logo)
	
	var wechat_lbl := Label.new()
	wechat_lbl.text = "微信"
	wechat_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	wechat_lbl.add_theme_font_size_override("font_size", theme.speech_font_size)
	wechat_lbl.add_theme_color_override("font_color", Color.WHITE)
	wechat_lbl.add_theme_color_override("font_outline_color", Color.BLACK)
	wechat_lbl.add_theme_constant_override("outline_size", 2)
	wechat_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
	wechat_box.add_child(wechat_lbl)
	
	grid.add_child(wechat_btn)
	
	# 2. Camera Icon Button
	var cam_btn := Button.new()
	cam_btn.custom_minimum_size = Vector2(48, 48)
	cam_btn.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	cam_btn.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	cam_btn.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	cam_btn.pressed.connect(_open_camera)
	
	var cam_box := VBoxContainer.new()
	cam_box.add_theme_constant_override("separation", 2)
	cam_box.set_anchors_preset(Control.PRESET_FULL_RECT)
	cam_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	cam_btn.add_child(cam_box)
	
	var cam_logo := Control.new()
	cam_logo.custom_minimum_size = Vector2(32, 32)
	cam_logo.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	cam_logo.mouse_filter = Control.MOUSE_FILTER_IGNORE
	cam_logo.connect("draw", _draw_camera_logo.bind(cam_logo))
	cam_box.add_child(cam_logo)
	
	var cam_lbl := Label.new()
	cam_lbl.text = "相机"
	cam_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	cam_lbl.add_theme_font_size_override("font_size", theme.speech_font_size)
	cam_lbl.add_theme_color_override("font_color", Color.WHITE)
	cam_lbl.add_theme_color_override("font_outline_color", Color.BLACK)
	cam_lbl.add_theme_constant_override("outline_size", 2)
	cam_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
	cam_box.add_child(cam_lbl)
	
	grid.add_child(cam_btn)
	
	# 3. Settings Icon Button (Dummy)
	var set_btn := Button.new()
	set_btn.custom_minimum_size = Vector2(48, 48)
	set_btn.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	set_btn.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	set_btn.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	
	var set_box := VBoxContainer.new()
	set_box.add_theme_constant_override("separation", 2)
	set_box.set_anchors_preset(Control.PRESET_FULL_RECT)
	set_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_btn.add_child(set_box)
	
	var set_logo := Control.new()
	set_logo.custom_minimum_size = Vector2(32, 32)
	set_logo.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	set_logo.mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_logo.connect("draw", _draw_settings_logo.bind(set_logo))
	set_box.add_child(set_logo)
	
	var set_lbl := Label.new()
	set_lbl.text = "设置"
	set_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	set_lbl.add_theme_font_size_override("font_size", theme.speech_font_size)
	set_lbl.add_theme_color_override("font_color", Color.WHITE)
	set_lbl.add_theme_color_override("font_outline_color", Color.BLACK)
	set_lbl.add_theme_constant_override("outline_size", 2)
	set_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_box.add_child(set_lbl)
	
	grid.add_child(set_btn)
	
	# 4. Gallery Icon Button
	var gal_btn := Button.new()
	gal_btn.custom_minimum_size = Vector2(48, 48)
	gal_btn.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	gal_btn.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	gal_btn.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	gal_btn.pressed.connect(_open_gallery)
	
	var gal_box := VBoxContainer.new()
	gal_box.add_theme_constant_override("separation", 2)
	gal_box.set_anchors_preset(Control.PRESET_FULL_RECT)
	gal_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	gal_btn.add_child(gal_box)
	
	var gal_logo := Control.new()
	gal_logo.custom_minimum_size = Vector2(32, 32)
	gal_logo.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	gal_logo.mouse_filter = Control.MOUSE_FILTER_IGNORE
	gal_logo.connect("draw", _draw_gallery_logo.bind(gal_logo))
	gal_box.add_child(gal_logo)
	
	var gal_lbl := Label.new()
	gal_lbl.text = "相册"
	gal_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	gal_lbl.add_theme_font_size_override("font_size", theme.speech_font_size)
	gal_lbl.add_theme_color_override("font_color", Color.WHITE)
	gal_lbl.add_theme_color_override("font_outline_color", Color.BLACK)
	gal_lbl.add_theme_constant_override("outline_size", 2)
	gal_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
	gal_box.add_child(gal_lbl)
	
	grid.add_child(gal_btn)

	# 5. Task List Icon Button
	var task_btn := Button.new()
	task_btn.custom_minimum_size = Vector2(48, 48)
	task_btn.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	task_btn.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	task_btn.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	task_btn.pressed.connect(_open_task)

	var task_box := VBoxContainer.new()
	task_box.add_theme_constant_override("separation", 2)
	task_box.set_anchors_preset(Control.PRESET_FULL_RECT)
	task_box.mouse_filter = Control.MOUSE_FILTER_IGNORE
	task_btn.add_child(task_box)

	var task_logo := Control.new()
	task_logo.custom_minimum_size = Vector2(32, 32)
	task_logo.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	task_logo.mouse_filter = Control.MOUSE_FILTER_IGNORE
	task_logo.connect("draw", _draw_task_logo.bind(task_logo))
	task_box.add_child(task_logo)

	var task_lbl := Label.new()
	task_lbl.text = "任务"
	task_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	task_lbl.add_theme_font_size_override("font_size", theme.speech_font_size)
	task_lbl.add_theme_color_override("font_color", Color.WHITE)
	task_lbl.add_theme_color_override("font_outline_color", Color.BLACK)
	task_lbl.add_theme_constant_override("outline_size", 2)
	task_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
	task_box.add_child(task_lbl)

	grid.add_child(task_btn)

	# Connection status at bottom
	_status_label = Label.new()
	_status_label.text = "后端已连接"
	_status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	_status_label.add_theme_color_override("font_color", Color(0.9, 0.9, 0.9, 0.75))
	_status_label.add_theme_color_override("font_outline_color", Color.BLACK)
	_status_label.add_theme_constant_override("outline_size", 2)
	_status_label.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_status_label.vertical_alignment = VERTICAL_ALIGNMENT_BOTTOM
	box.add_child(_status_label)
	
	return margin_container


func _draw_home_screen(box: Control) -> void:
	var size := box.size
	# Draw pixel art wallpaper: sunset/cyberpunk gradient
	var steps := 20
	var step_h := size.y / steps
	var color_top := Color(0.18, 0.12, 0.32)
	var color_bottom := Color(0.70, 0.30, 0.40)
	for i in range(steps):
		var t := float(i) / float(steps)
		var c := color_top.lerp(color_bottom, t)
		box.draw_rect(Rect2(0, i * step_h, size.x, step_h), c)


func _draw_wechat_logo(canvas: Control) -> void:
	var green_color := Color(0.16, 0.68, 0.30)
	var border_color := Color(0.08, 0.38, 0.16)
	
	# Rounded green background box
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(0.0, 0.0, 32.0, 32.0), border_color)
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(1.0, 1.0, 30.0, 30.0), green_color)
	
	# Big Bubble
	var big_c := Color(1.0, 1.0, 1.0)
	canvas.draw_rect(Rect2(6, 9, 14, 10), big_c)
	canvas.draw_rect(Rect2(7, 8, 12, 12), big_c)
	# Eyes
	canvas.draw_rect(Rect2(10, 12, 1, 2), border_color)
	canvas.draw_rect(Rect2(15, 12, 1, 2), border_color)
	
	# Small Bubble
	canvas.draw_rect(Rect2(15, 15, 12, 8), big_c)
	canvas.draw_rect(Rect2(16, 14, 10, 10), big_c)
	canvas.draw_rect(Rect2(14, 15, 1, 8), border_color)
	# Eyes
	canvas.draw_rect(Rect2(18, 18, 1, 2), border_color)
	canvas.draw_rect(Rect2(22, 18, 1, 2), border_color)
	
	# Tails
	canvas.draw_rect(Rect2(8, 18, 2, 2), big_c)
	canvas.draw_rect(Rect2(6, 19, 2, 1), big_c)
	canvas.draw_rect(Rect2(23, 22, 2, 2), big_c)
	canvas.draw_rect(Rect2(25, 23, 2, 1), big_c)


func _draw_camera_logo(canvas: Control) -> void:
	var border_color := Color(0.12, 0.12, 0.12)
	var bg_color := Color(0.62, 0.65, 0.68)
	
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(0.0, 0.0, 32.0, 32.0), border_color)
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(1.0, 1.0, 30.0, 30.0), bg_color)
	
	var cam_color := Color(0.28, 0.30, 0.33)
	canvas.draw_rect(Rect2(6, 11, 20, 13), cam_color)
	canvas.draw_rect(Rect2(10, 8, 12, 3), cam_color)
	
	canvas.draw_rect(Rect2(12, 13, 8, 8), border_color)
	canvas.draw_rect(Rect2(13, 14, 6, 6), Color(0.1, 0.52, 0.85))
	canvas.draw_rect(Rect2(21, 13, 2, 2), Color(0.9, 0.2, 0.2))


func _draw_settings_logo(canvas: Control) -> void:
	var border_color := Color(0.12, 0.12, 0.12)
	var bg_color := Color(0.35, 0.42, 0.48)
	
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(0.0, 0.0, 32.0, 32.0), border_color)
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(1.0, 1.0, 30.0, 30.0), bg_color)
	
	var gear_color := Color(0.72, 0.74, 0.76)
	canvas.draw_rect(Rect2(12, 8, 8, 16), gear_color)
	canvas.draw_rect(Rect2(8, 12, 16, 8), gear_color)
	canvas.draw_rect(Rect2(10, 10, 12, 12), gear_color)
	canvas.draw_rect(Rect2(14, 14, 4, 4), border_color)


func _build_chat_screen() -> Control:
	var margin_container := MarginContainer.new()
	margin_container.name = "ChatScreen"
	margin_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	margin_container.add_theme_constant_override("margin_left", 6)
	margin_container.add_theme_constant_override("margin_right", 6)
	margin_container.add_theme_constant_override("margin_top", 18) # accommodates status bar
	margin_container.add_theme_constant_override("margin_bottom", 6)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	margin_container.add_child(box)

	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	header.connect("draw", _draw_wechat_header.bind(header))
	box.add_child(header)

	var back_button := Button.new()
	back_button.custom_minimum_size = Vector2(24, 20)
	back_button.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	back_button.connect("draw", _draw_back_button.bind(back_button))
	back_button.pressed.connect(_close_chat)
	header.add_child(back_button)

	var title := Label.new()
	title.text = "公司工作群 (%d)" % (_worker_profiles.size() + 1)
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)

	_message_scroll = ScrollContainer.new()
	_message_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_message_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(_message_scroll)

	_message_list = VBoxContainer.new()
	_message_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_message_list.add_theme_constant_override("separation", 6)
	_message_scroll.add_child(_message_list)

	# WeChat Bottom Input Bar (1:1 styling replica)
	var input_panel := PanelContainer.new()
	var input_panel_style := StyleBoxFlat.new()
	input_panel_style.bg_color = Color(0.94, 0.94, 0.94)
	input_panel_style.border_color = Color(0.85, 0.85, 0.85)
	input_panel_style.set_border_width_all(0)
	input_panel_style.border_width_top = 1 # WeChat top border of bottom bar
	input_panel_style.set_content_margin(SIDE_LEFT, 6)
	input_panel_style.set_content_margin(SIDE_RIGHT, 6)
	input_panel_style.set_content_margin(SIDE_TOP, 6)
	input_panel_style.set_content_margin(SIDE_BOTTOM, 6)
	input_panel.add_theme_stylebox_override("panel", input_panel_style)
	box.add_child(input_panel)

	var input_row := HBoxContainer.new()
	input_row.add_theme_constant_override("separation", 4)
	input_panel.add_child(input_row)

	var mention_button := Button.new()
	mention_button.text = "@"
	mention_button.custom_minimum_size = Vector2(24, 20)
	
	var wechat_btn_style := StyleBoxFlat.new()
	wechat_btn_style.bg_color = Color(0.98, 0.98, 0.98)
	wechat_btn_style.border_color = Color(0.78, 0.78, 0.78)
	wechat_btn_style.set_border_width_all(1)
	wechat_btn_style.set_corner_radius_all(0) # Sharp pixel corners!
	wechat_btn_style.set_content_margin(SIDE_LEFT, 5)
	wechat_btn_style.set_content_margin(SIDE_RIGHT, 5)
	
	var wechat_btn_pressed := wechat_btn_style.duplicate()
	wechat_btn_pressed.bg_color = Color(0.85, 0.85, 0.85)
	
	mention_button.add_theme_stylebox_override("normal", wechat_btn_style)
	mention_button.add_theme_stylebox_override("hover", wechat_btn_style)
	mention_button.add_theme_stylebox_override("pressed", wechat_btn_pressed)
	mention_button.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	mention_button.pressed.connect(_toggle_mention_popup)
	input_row.add_child(mention_button)

	_chat_input = LineEdit.new()
	_chat_input.placeholder_text = "发消息给项目组…"
	_chat_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_input.text_submitted.connect(_on_text_submitted)
	_chat_input.text_changed.connect(_on_chat_input_changed)
	
	var chat_input_normal := StyleBoxFlat.new()
	chat_input_normal.bg_color = Color(1.0, 1.0, 1.0) # White input background
	chat_input_normal.border_color = Color(0.8, 0.8, 0.8)
	chat_input_normal.set_border_width_all(1)
	chat_input_normal.set_corner_radius_all(0) # Sharp pixel corners!
	chat_input_normal.set_content_margin(SIDE_LEFT, 6)
	chat_input_normal.set_content_margin(SIDE_RIGHT, 6)
	chat_input_normal.set_content_margin(SIDE_TOP, 3)
	chat_input_normal.set_content_margin(SIDE_BOTTOM, 3)
	
	var chat_input_focus := chat_input_normal.duplicate()
	chat_input_focus.border_color = Color(0.16, 0.68, 0.30) # WeChat green focus
	
	_chat_input.add_theme_stylebox_override("normal", chat_input_normal)
	_chat_input.add_theme_stylebox_override("focus", chat_input_focus)
	_chat_input.add_theme_color_override("font_color", Color(0.1, 0.1, 0.1)) # Dark text
	_chat_input.add_theme_color_override("font_placeholder_color", Color(0.6, 0.6, 0.6))
	input_row.add_child(_chat_input)

	_send_button = Button.new()
	_send_button.text = "+"
	_send_button.add_theme_stylebox_override("normal", wechat_btn_style)
	_send_button.add_theme_stylebox_override("hover", wechat_btn_style)
	_send_button.add_theme_stylebox_override("pressed", wechat_btn_pressed)
	_send_button.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	_send_button.pressed.connect(_submit)
	input_row.add_child(_send_button)
	
	return margin_container


func _draw_wechat_header(canvas: Control) -> void:
	var size := canvas.size
	var bg_color := Color(0.94, 0.94, 0.94)
	canvas.draw_rect(Rect2(0, 0, size.x, size.y), bg_color)
	canvas.draw_rect(Rect2(0, size.y - 1, size.x, 1), Color(0.78, 0.78, 0.78))


func _draw_back_button(btn: Button) -> void:
	var color := Color(0.12, 0.12, 0.12)
	# Draw chevron chevron '<'
	btn.draw_rect(Rect2(10, 6, 2, 2), color)
	btn.draw_rect(Rect2(8, 8, 2, 2), color)
	btn.draw_rect(Rect2(6, 10, 2, 2), color)
	btn.draw_rect(Rect2(8, 12, 2, 2), color)
	btn.draw_rect(Rect2(10, 14, 2, 2), color)


func _build_mention_popup() -> PanelContainer:
	var popup := PanelContainer.new()
	popup.name = "MentionPopup"
	popup.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	popup.offset_bottom = -32.0 # align exactly above bottom input bar (which is 32px high)
	popup.grow_vertical = Control.GROW_DIRECTION_BEGIN
	
	var popup_style := StyleBoxFlat.new()
	popup_style.bg_color = Color(1.0, 1.0, 1.0)
	popup_style.border_color = Color(0.8, 0.8, 0.8)
	popup_style.set_border_width_all(1)
	popup_style.set_corner_radius_all(0) # Sharp pixel corners!
	popup.add_theme_stylebox_override("panel", popup_style)

	var layout := VBoxContainer.new()
	layout.add_theme_constant_override("separation", 0)
	popup.add_child(layout)

	# Header bar with back button
	var header_bar := HBoxContainer.new()
	header_bar.add_theme_constant_override("separation", 6)
	header_bar.connect("draw", _draw_wechat_header.bind(header_bar))
	layout.add_child(header_bar)

	var back_button := Button.new()
	back_button.custom_minimum_size = Vector2(24, 20)
	back_button.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	back_button.connect("draw", _draw_back_button.bind(back_button))
	back_button.pressed.connect(func(): popup.visible = false)
	header_bar.add_child(back_button)

	var title := Label.new()
	title.text = "选择提醒的人"
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header_bar.add_child(title)

	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	layout.add_child(scroll)

	var list := VBoxContainer.new()
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	list.add_theme_constant_override("separation", 0) # Tight separator
	scroll.add_child(list)

	# Item stylebox
	var item_normal := StyleBoxFlat.new()
	item_normal.bg_color = Color(1.0, 1.0, 1.0)
	item_normal.border_color = Color(0.92, 0.92, 0.92)
	item_normal.set_border_width_all(0)
	item_normal.border_width_bottom = 1
	item_normal.set_corner_radius_all(0)
	item_normal.set_content_margin(SIDE_LEFT, 6)
	item_normal.set_content_margin(SIDE_RIGHT, 6)
	item_normal.set_content_margin(SIDE_TOP, 5)
	item_normal.set_content_margin(SIDE_BOTTOM, 5)

	var item_hover := item_normal.duplicate()
	item_hover.bg_color = Color(0.94, 0.94, 0.94)

	for worker_id in _worker_profiles.keys():
		var profile: Dictionary = _worker_profiles[worker_id]
		var item_btn := Button.new()
		item_btn.custom_minimum_size = Vector2(0, 24) # Set minimum height to prevent overlapping
		item_btn.add_theme_stylebox_override("normal", item_normal)
		item_btn.add_theme_stylebox_override("hover", item_hover)
		item_btn.add_theme_stylebox_override("pressed", item_hover)
		item_btn.pressed.connect(_pick_mention.bind(str(worker_id)))
		
		# HBox inside item_btn
		var item_row := HBoxContainer.new()
		item_row.add_theme_constant_override("separation", 6)
		item_row.set_anchors_preset(Control.PRESET_FULL_RECT)
		item_row.offset_left = 6
		item_row.offset_right = -6
		item_row.offset_top = 2
		item_row.offset_bottom = -2
		item_row.mouse_filter = Control.MOUSE_FILTER_IGNORE # Fix click detection on list
		item_btn.add_child(item_row)
		
		# Avatar
		var avatar_rect := TextureRect.new()
		avatar_rect.custom_minimum_size = Vector2(16, 16)
		avatar_rect.stretch_mode = TextureRect.STRETCH_SCALE
		avatar_rect.texture_filter = Control.TEXTURE_FILTER_NEAREST
		avatar_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
		avatar_rect.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		
		var tex := _find_texture_for_sender(worker_id)
		if tex != null:
			var atlas := AtlasTexture.new()
			atlas.atlas = tex
			atlas.region = Rect2(0, 9, 16, 16)
			avatar_rect.texture = atlas
		item_row.add_child(avatar_rect)
		
		# Name Label
		var name_lbl := Label.new()
		name_lbl.text = str(profile.get("name", worker_id))
		name_lbl.add_theme_font_size_override("font_size", theme.detail_font_size)
		name_lbl.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
		name_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
		item_row.add_child(name_lbl)
		
		# Dot separator
		var dot_lbl := Label.new()
		dot_lbl.text = "·"
		dot_lbl.add_theme_font_size_override("font_size", theme.detail_font_size)
		dot_lbl.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
		dot_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
		item_row.add_child(dot_lbl)
		
		# Role Label
		var role_lbl := Label.new()
		role_lbl.text = str(profile.get("role", ""))
		role_lbl.add_theme_font_size_override("font_size", theme.detail_font_size - 1)
		role_lbl.add_theme_color_override("font_color", Color(0.5, 0.5, 0.5))
		role_lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
		item_row.add_child(role_lbl)
		
		list.add_child(item_btn)
	return popup


func _open_chat() -> void:
	_home_screen.visible = false
	_chat_screen.visible = true
	_scroll_messages_to_bottom()


func _close_chat() -> void:
	_chat_screen.visible = false
	_mention_popup.visible = false
	_home_screen.visible = true


func _toggle_mention_popup() -> void:
	_mention_popup.visible = !_mention_popup.visible


func _pick_mention(worker_id: String) -> void:
	_mention_popup.visible = false
	var profile: Dictionary = _worker_profiles.get(worker_id, {})
	var name := str(profile.get("name", worker_id))
	if !_mention_ids.has(worker_id):
		_mention_ids.append(worker_id)
	_chat_input.text += "@%s " % name
	_chat_input.caret_column = _chat_input.text.length()
	_chat_input.grab_focus()
	_on_chat_input_changed(_chat_input.text)


func _on_chat_input_changed(new_text: String) -> void:
	if _send_button != null:
		if new_text.strip_edges().is_empty():
			_send_button.text = "+"
			
			var wechat_btn_style := StyleBoxFlat.new()
			wechat_btn_style.bg_color = Color(0.98, 0.98, 0.98)
			wechat_btn_style.border_color = Color(0.78, 0.78, 0.78)
			wechat_btn_style.set_border_width_all(1)
			wechat_btn_style.set_corner_radius_all(0) # Sharp corners
			wechat_btn_style.set_content_margin(SIDE_LEFT, 5)
			wechat_btn_style.set_content_margin(SIDE_RIGHT, 5)
			
			var wechat_btn_pressed := wechat_btn_style.duplicate()
			wechat_btn_pressed.bg_color = Color(0.85, 0.85, 0.85)
			
			_send_button.add_theme_stylebox_override("normal", wechat_btn_style)
			_send_button.add_theme_stylebox_override("hover", wechat_btn_style)
			_send_button.add_theme_stylebox_override("pressed", wechat_btn_pressed)
			_send_button.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
		else:
			_send_button.text = "发送"
			
			var green_style := StyleBoxFlat.new()
			green_style.bg_color = Color(0.16, 0.68, 0.30)
			green_style.border_color = Color(0.08, 0.40, 0.18)
			green_style.set_border_width_all(1)
			green_style.set_corner_radius_all(0) # Sharp corners
			green_style.set_content_margin(SIDE_LEFT, 5)
			green_style.set_content_margin(SIDE_RIGHT, 5)
			
			var green_pressed := green_style.duplicate()
			green_pressed.bg_color = Color(0.10, 0.50, 0.22)
			
			_send_button.add_theme_stylebox_override("normal", green_style)
			_send_button.add_theme_stylebox_override("hover", green_style)
			_send_button.add_theme_stylebox_override("pressed", green_pressed)
			_send_button.add_theme_color_override("font_color", Color.WHITE)


func _on_text_submitted(_text: String) -> void:
	_submit()


func _submit() -> void:
	var text := _chat_input.text.strip_edges()
	if text.is_empty():
		return
	var target_ids := _resolve_mentions(text)
	add_chat_message("老板", text, true)
	command_submitted.emit(text, target_ids)
	_chat_input.clear()
	_mention_ids.clear()
	_on_chat_input_changed("")


func _resolve_mentions(text: String) -> Array[String]:
	var resolved: Array[String] = []
	for worker_id in _worker_profiles.keys():
		var profile: Dictionary = _worker_profiles[worker_id]
		if text.contains("@%s" % str(profile.get("name", ""))):
			resolved.append(str(worker_id))
	return resolved


func add_chat_message(sender_name: String, text: String, is_boss: bool = false) -> void:
	if _message_list == null or text.strip_edges().is_empty():
		return
		
	var display_name := _get_display_name(sender_name)
	var entry := VBoxContainer.new()
	entry.add_theme_constant_override("separation", 1)

	var name_label := Label.new()
	name_label.text = display_name
	name_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	name_label.add_theme_color_override("font_color", theme.placeholder_color)
	if is_boss:
		name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	entry.add_child(name_label)

	var bubble_row := HBoxContainer.new()
	bubble_row.add_theme_constant_override("separation", 4)
	entry.add_child(bubble_row)

	var avatar_size := Vector2(24, 24)
	var avatar := Control.new()
	avatar.custom_minimum_size = avatar_size
	avatar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	
	var tex := _find_texture_for_sender(sender_name)
	if tex != null:
		var tex_rect := TextureRect.new()
		tex_rect.set_anchors_preset(Control.PRESET_FULL_RECT)
		tex_rect.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tex_rect.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		tex_rect.texture_filter = Control.TEXTURE_FILTER_NEAREST
		
		var atlas := AtlasTexture.new()
		atlas.atlas = tex
		atlas.region = Rect2(0, 9, 16, 16) # top half of col 0, row 0 frame (the face)
		tex_rect.texture = atlas
		
		avatar.add_child(tex_rect)
	else:
		avatar.connect("draw", _draw_boss_avatar.bind(avatar))

	var bubble := PanelContainer.new()
	var bubble_style := StyleBoxEmpty.new()
	# Set margins for text, leaving space for pointer
	if is_boss:
		bubble_style.set_content_margin(SIDE_LEFT, 6.0)
		bubble_style.set_content_margin(SIDE_TOP, 5.0)
		bubble_style.set_content_margin(SIDE_RIGHT, 10.0)
		bubble_style.set_content_margin(SIDE_BOTTOM, 5.0)
	else:
		bubble_style.set_content_margin(SIDE_LEFT, 10.0)
		bubble_style.set_content_margin(SIDE_TOP, 5.0)
		bubble_style.set_content_margin(SIDE_RIGHT, 6.0)
		bubble_style.set_content_margin(SIDE_BOTTOM, 5.0)
	bubble.add_theme_stylebox_override("panel", bubble_style)
	bubble.connect("draw", _draw_wechat_bubble.bind(bubble, is_boss))

	var label := Label.new()
	label.text = text.substr(0, 220)
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", theme.detail_font_size)
	label.add_theme_color_override("font_color", BUBBLE_TEXT)
	bubble.add_child(label)
	bubble.custom_minimum_size = Vector2(minf(140.0, float(text.length()) * float(theme.detail_font_size)), 0)

	if is_boss:
		# Spacer first, then bubble, then avatar
		var spacer := Control.new()
		spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		bubble_row.add_child(spacer)
		bubble_row.add_child(bubble)
		bubble_row.add_child(avatar)
	else:
		# Avatar first, then bubble, then spacer
		bubble_row.add_child(avatar)
		bubble_row.add_child(bubble)
		var spacer := Control.new()
		spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		bubble_row.add_child(spacer)

	_message_list.add_child(entry)
	if _message_list.get_child_count() > 60:
		_message_list.get_child(0).queue_free()
	_scroll_messages_to_bottom()


func _draw_boss_avatar(canvas: Control) -> void:
	var size := canvas.size
	var bg_color := Color(0.18, 0.16, 0.14)
	canvas.draw_rect(Rect2(0, 0, size.x, size.y), bg_color)
	canvas.draw_rect(Rect2(1, 1, size.x - 2, size.y - 2), Color(0.85, 0.70, 0.25), false, 1.0)
	
	var crown_color := Color(0.95, 0.82, 0.22)
	canvas.draw_rect(Rect2(7, 15, 10, 2), crown_color)
	canvas.draw_rect(Rect2(7, 10, 2, 5), crown_color)
	canvas.draw_rect(Rect2(11, 8, 2, 7), crown_color)
	canvas.draw_rect(Rect2(15, 10, 2, 5), crown_color)
	canvas.draw_rect(Rect2(7, 9, 2, 1), Color(0.9, 0.2, 0.2))
	canvas.draw_rect(Rect2(11, 7, 2, 1), Color(0.2, 0.6, 0.9))
	canvas.draw_rect(Rect2(15, 9, 2, 1), Color(0.9, 0.2, 0.2))


func _draw_wechat_bubble(canvas: PanelContainer, is_boss: bool) -> void:
	var size := canvas.size
	var bg_color := Color(0.57, 0.89, 0.44) if is_boss else Color(0.98, 0.98, 0.98)
	var border_color := Color(0.15, 0.55, 0.15) if is_boss else Color(0.78, 0.78, 0.78)
	
	if is_boss:
		# Boss bubble (pointer on the right, body takes size.x - 4)
		var body_w := size.x - 4.0
		_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(0.0, 0.0, body_w, size.y), border_color)
		_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(1.0, 1.0, body_w - 2.0, size.y - 2.0), bg_color)
		
		# Pointer pointing right
		canvas.draw_rect(Rect2(size.x - 4.0, 8, 4, 1), border_color)
		canvas.draw_rect(Rect2(size.x - 4.0, 9, 3, 1), border_color)
		canvas.draw_rect(Rect2(size.x - 4.0, 10, 2, 1), border_color)
		canvas.draw_rect(Rect2(size.x - 4.0, 11, 1, 1), border_color)
		
		canvas.draw_rect(Rect2(size.x - 4.0, 9, 2, 1), bg_color)
		canvas.draw_rect(Rect2(size.x - 4.0, 10, 1, 1), bg_color)
	else:
		# Worker bubble (pointer on the left, body takes size.x - 4 starting from x=4)
		var body_w := size.x - 4.0
		_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(4.0, 0.0, body_w, size.y), border_color)
		_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(5.0, 1.0, body_w - 2.0, size.y - 2.0), bg_color)
		
		# Pointer pointing left
		canvas.draw_rect(Rect2(0.0, 8, 4, 1), border_color)
		canvas.draw_rect(Rect2(1.0, 9, 3, 1), border_color)
		canvas.draw_rect(Rect2(2.0, 10, 2, 1), border_color)
		canvas.draw_rect(Rect2(3.0, 11, 1, 1), border_color)
		
		canvas.draw_rect(Rect2(2.0, 9, 2, 1), bg_color)
		canvas.draw_rect(Rect2(3.0, 10, 1, 1), bg_color)


func _find_texture_for_sender(sender_name: String) -> Texture2D:
	if sender_name == "老板":
		return null
		
	for worker in get_tree().get_nodes_in_group("demo_workers"):
		if worker.name == sender_name:
			return worker.character_texture as Texture2D
			
	for worker_id in _worker_profiles.keys():
		var profile: Dictionary = _worker_profiles[worker_id]
		var display_name := str(profile.get("name", ""))
		if display_name == sender_name:
			for worker in get_tree().get_nodes_in_group("demo_workers"):
				if worker.name == worker_id:
					return worker.character_texture as Texture2D
					
	return null


func _get_display_name(sender_name: String) -> String:
	if sender_name == "老板":
		return "老板"
	if _worker_profiles.has(sender_name):
		return str(_worker_profiles[sender_name].get("name", sender_name))
	return sender_name


func set_status(text: String) -> void:
	if _status_label != null:
		_status_label.text = text


func status_text() -> String:
	if _status_label != null:
		return _status_label.text
	return ""


func _scroll_messages_to_bottom() -> void:
	if _message_scroll == null:
		return
	await get_tree().process_frame
	var bar := _message_scroll.get_v_scroll_bar()
	if bar != null:
		_message_scroll.scroll_vertical = int(bar.max_value)


func _draw_gallery_logo(canvas: Control) -> void:
	var border_color := Color(0.12, 0.12, 0.12)
	var bg_color := Color(0.2, 0.6, 0.86) # Light blue sky background
	
	# Rounded box
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(0.0, 0.0, 32.0, 32.0), border_color)
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(1.0, 1.0, 30.0, 30.0), bg_color)
	
	# Sun (yellow dot)
	canvas.draw_rect(Rect2(20, 6, 4, 4), Color(0.98, 0.82, 0.2))
	
	# Mountains (deep green pixel shapes)
	var mount_color := Color(0.25, 0.55, 0.35)
	canvas.draw_rect(Rect2(4, 18, 12, 10), mount_color)
	canvas.draw_rect(Rect2(6, 15, 8, 3), mount_color)
	canvas.draw_rect(Rect2(8, 12, 4, 3), mount_color)
	
	var mount_color2 := Color(0.18, 0.42, 0.26)
	canvas.draw_rect(Rect2(12, 21, 16, 7), mount_color2)
	canvas.draw_rect(Rect2(14, 17, 12, 4), mount_color2)
	canvas.draw_rect(Rect2(18, 14, 4, 3), mount_color2)


func _build_camera_screen() -> Control:
	var margin_container := MarginContainer.new()
	margin_container.name = "CameraScreen"
	margin_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	margin_container.add_theme_constant_override("margin_left", 6)
	margin_container.add_theme_constant_override("margin_right", 6)
	margin_container.add_theme_constant_override("margin_top", 18) # accommodates status bar
	margin_container.add_theme_constant_override("margin_bottom", 6)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 0)
	margin_container.add_child(box)

	# 1. Viewfinder (取景器)
	var view_panel := PanelContainer.new()
	view_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var panel_style := StyleBoxFlat.new()
	panel_style.bg_color = Color(0.0, 0.0, 0.0) # Black border for camera
	panel_style.border_color = Color(0.15, 0.15, 0.15)
	panel_style.set_border_width_all(1)
	panel_style.set_corner_radius_all(0)
	view_panel.add_theme_stylebox_override("panel", panel_style)
	box.add_child(view_panel)

	var view_center := CenterContainer.new()
	view_panel.add_child(view_center)

	var viewport_container := SubViewportContainer.new()
	viewport_container.custom_minimum_size = Vector2(196, 196) # square camera screen
	viewport_container.stretch = true
	view_center.add_child(viewport_container)

	_sub_viewport = SubViewport.new()
	_sub_viewport.size = Vector2i(196, 196)
	_sub_viewport.canvas_item_default_texture_filter = SubViewport.DEFAULT_CANVAS_ITEM_TEXTURE_FILTER_NEAREST
	# Share 2D world so it renders Left Main game scene!
	_sub_viewport.world_2d = get_viewport().find_world_2d()
	viewport_container.add_child(_sub_viewport)

	_sub_camera = Camera2D.new()
	_sub_camera.zoom = Vector2(1.5, 1.5) # Zoom in a bit for camera feel
	_sub_viewport.add_child(_sub_camera)

	# Viewfinder Grid overlay
	var grid_overlay := Control.new()
	grid_overlay.set_anchors_preset(Control.PRESET_FULL_RECT)
	grid_overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	grid_overlay.connect("draw", func():
		var size := grid_overlay.size
		var line_color := Color(1.0, 1.0, 1.0, 0.22)
		# 3x3 Grid
		grid_overlay.draw_line(Vector2(size.x / 3.0, 0), Vector2(size.x / 3.0, size.y), line_color, 1.0)
		grid_overlay.draw_line(Vector2(size.x * 2.0 / 3.0, 0), Vector2(size.x * 2.0 / 3.0, size.y), line_color, 1.0)
		grid_overlay.draw_line(Vector2(0, size.y / 3.0), Vector2(size.x, size.y / 3.0), line_color, 1.0)
		grid_overlay.draw_line(Vector2(0, size.y * 2.0 / 3.0), Vector2(size.x, size.y * 2.0 / 3.0), line_color, 1.0)
	)
	viewport_container.add_child(grid_overlay)

	# Camera Flash effect (Overlay on viewport)
	_flash_rect = ColorRect.new()
	_flash_rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	_flash_rect.color = Color(1, 1, 1, 0)
	_flash_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	viewport_container.add_child(_flash_rect)

	# 2. Control Panel (快门等控制栏)
	var ctrl_panel := PanelContainer.new()
	var ctrl_style := StyleBoxFlat.new()
	ctrl_style.bg_color = Color(0.08, 0.08, 0.08)
	ctrl_style.set_content_margin(SIDE_LEFT, 10)
	ctrl_style.set_content_margin(SIDE_RIGHT, 10)
	ctrl_style.set_content_margin(SIDE_TOP, 8)
	ctrl_style.set_content_margin(SIDE_BOTTOM, 8)
	ctrl_panel.add_theme_stylebox_override("panel", ctrl_style)
	box.add_child(ctrl_panel)

	var ctrl_row := HBoxContainer.new()
	ctrl_row.alignment = BoxContainer.ALIGNMENT_CENTER
	ctrl_row.add_theme_constant_override("separation", 24)
	ctrl_panel.add_child(ctrl_row)

	# Left: Photo Thumbnail Button (Links to Album)
	var prev_btn := Button.new()
	prev_btn.custom_minimum_size = Vector2(28, 28)
	prev_btn.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	
	var prev_style := StyleBoxFlat.new()
	prev_style.bg_color = Color(0.2, 0.2, 0.2)
	prev_style.border_color = Color(0.4, 0.4, 0.4)
	prev_style.set_border_width_all(1)
	prev_style.set_corner_radius_all(0)
	prev_btn.add_theme_stylebox_override("normal", prev_style)
	prev_btn.pressed.connect(_open_gallery)
	ctrl_row.add_child(prev_btn)

	_shutter_preview_rect = TextureRect.new()
	_shutter_preview_rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	_shutter_preview_rect.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_shutter_preview_rect.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	_shutter_preview_rect.texture_filter = Control.TEXTURE_FILTER_NEAREST
	_shutter_preview_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	prev_btn.add_child(_shutter_preview_rect)

	# Center: Round Shutter Button
	var shutter_btn := Button.new()
	shutter_btn.custom_minimum_size = Vector2(40, 40)
	
	var shut_style := StyleBoxFlat.new()
	shut_style.bg_color = Color(1.0, 1.0, 1.0)
	shut_style.border_color = Color(0.7, 0.7, 0.7)
	shut_style.set_border_width_all(2)
	shut_style.set_corner_radius_all(20) # Round!
	
	var shut_style_pressed := shut_style.duplicate()
	shut_style_pressed.bg_color = Color(0.85, 0.85, 0.85)

	shutter_btn.add_theme_stylebox_override("normal", shut_style)
	shutter_btn.add_theme_stylebox_override("hover", shut_style)
	shutter_btn.add_theme_stylebox_override("pressed", shut_style_pressed)
	shutter_btn.pressed.connect(_take_photo)
	ctrl_row.add_child(shutter_btn)

	# Right: Close / Back Button
	var close_btn := Button.new()
	close_btn.text = "返回"
	close_btn.custom_minimum_size = Vector2(32, 28)
	close_btn.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	
	var close_style := StyleBoxFlat.new()
	close_style.bg_color = Color(0.18, 0.18, 0.18)
	close_style.border_color = Color(0.3, 0.3, 0.3)
	close_style.set_border_width_all(1)
	close_style.set_corner_radius_all(0)
	close_style.set_content_margin(SIDE_LEFT, 6)
	close_style.set_content_margin(SIDE_RIGHT, 6)
	
	var close_style_pressed := close_style.duplicate()
	close_style_pressed.bg_color = Color(0.12, 0.12, 0.12)
	
	close_btn.add_theme_stylebox_override("normal", close_style)
	close_btn.add_theme_stylebox_override("hover", close_style)
	close_btn.add_theme_stylebox_override("pressed", close_style_pressed)
	close_btn.add_theme_color_override("font_color", Color.WHITE)
	close_btn.add_theme_font_size_override("font_size", theme.speech_font_size)
	close_btn.pressed.connect(_close_camera)
	ctrl_row.add_child(close_btn)

	return margin_container


func _build_gallery_screen() -> Control:
	var margin_container := MarginContainer.new()
	margin_container.name = "GalleryScreen"
	margin_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	margin_container.add_theme_constant_override("margin_left", 6)
	margin_container.add_theme_constant_override("margin_right", 6)
	margin_container.add_theme_constant_override("margin_top", 18) # accommodates status bar
	margin_container.add_theme_constant_override("margin_bottom", 6)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	margin_container.add_child(box)

	# Header (with back button)
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	header.connect("draw", _draw_wechat_header.bind(header))
	box.add_child(header)

	var back_button := Button.new()
	back_button.custom_minimum_size = Vector2(24, 20)
	back_button.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	back_button.connect("draw", _draw_back_button.bind(back_button))
	back_button.pressed.connect(_close_gallery)
	header.add_child(back_button)

	var title := Label.new()
	title.text = "手机相册"
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)

	# Scroll Container
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(scroll)

	# Grid Container
	_gallery_grid = GridContainer.new()
	_gallery_grid.columns = 3
	_gallery_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_gallery_grid.add_theme_constant_override("h_separation", 4)
	_gallery_grid.add_theme_constant_override("v_separation", 4)
	scroll.add_child(_gallery_grid)

	return margin_container


func _build_photo_preview_screen() -> Control:
	var margin_container := MarginContainer.new()
	margin_container.name = "PhotoPreviewScreen"
	margin_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	margin_container.add_theme_constant_override("margin_left", 0)
	margin_container.add_theme_constant_override("margin_right", 0)
	margin_container.add_theme_constant_override("margin_top", 18) # accommodates status bar
	margin_container.add_theme_constant_override("margin_bottom", 0)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 0)
	margin_container.add_child(box)

	# Header (with back button)
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	header.connect("draw", _draw_wechat_header.bind(header))
	box.add_child(header)

	var back_button := Button.new()
	back_button.custom_minimum_size = Vector2(24, 20)
	back_button.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	back_button.connect("draw", _draw_back_button.bind(back_button))
	back_button.pressed.connect(_close_photo_preview)
	header.add_child(back_button)

	var title := Label.new()
	title.text = "照片预览"
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)

	# Main Image Viewer
	var center := CenterContainer.new()
	center.size_flags_vertical = Control.SIZE_EXPAND_FILL
	center.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(center)

	_preview_texture_rect = TextureRect.new()
	_preview_texture_rect.custom_minimum_size = Vector2(210, 118) # 16:9 proportion fits nicely
	_preview_texture_rect.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_preview_texture_rect.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_preview_texture_rect.texture_filter = Control.TEXTURE_FILTER_NEAREST
	center.add_child(_preview_texture_rect)

	# Bottom toolbar with Delete button
	var toolbar := PanelContainer.new()
	var toolbar_style := StyleBoxFlat.new()
	toolbar_style.bg_color = Color(0.94, 0.94, 0.94)
	toolbar_style.border_color = Color(0.85, 0.85, 0.85)
	toolbar_style.set_border_width_all(0)
	toolbar_style.border_width_top = 1
	toolbar_style.set_content_margin(SIDE_TOP, 6)
	toolbar_style.set_content_margin(SIDE_BOTTOM, 6)
	toolbar.add_theme_stylebox_override("panel", toolbar_style)
	box.add_child(toolbar)

	var toolbar_row := HBoxContainer.new()
	toolbar_row.alignment = BoxContainer.ALIGNMENT_CENTER
	toolbar.add_child(toolbar_row)

	var delete_btn := Button.new()
	delete_btn.text = "删除照片"
	delete_btn.custom_minimum_size = Vector2(80, 24)
	
	var del_style := StyleBoxFlat.new()
	del_style.bg_color = Color(0.85, 0.2, 0.2)
	del_style.border_color = Color(0.6, 0.1, 0.1)
	del_style.set_border_width_all(1)
	del_style.set_corner_radius_all(0)
	del_style.set_content_margin(SIDE_LEFT, 8)
	del_style.set_content_margin(SIDE_RIGHT, 8)
	
	var del_style_pressed := del_style.duplicate()
	del_style_pressed.bg_color = Color(0.65, 0.1, 0.1)
	
	delete_btn.add_theme_stylebox_override("normal", del_style)
	delete_btn.add_theme_stylebox_override("hover", del_style)
	delete_btn.add_theme_stylebox_override("pressed", del_style_pressed)
	delete_btn.add_theme_color_override("font_color", Color.WHITE)
	delete_btn.pressed.connect(_delete_current_photo)
	toolbar_row.add_child(delete_btn)

	return margin_container


func _build_task_screen() -> Control:
	var margin_container := MarginContainer.new()
	margin_container.name = "TaskScreen"
	margin_container.set_anchors_preset(Control.PRESET_FULL_RECT)
	margin_container.add_theme_constant_override("margin_left", 6)
	margin_container.add_theme_constant_override("margin_right", 6)
	margin_container.add_theme_constant_override("margin_top", 18)
	margin_container.add_theme_constant_override("margin_bottom", 6)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 4)
	margin_container.add_child(box)

	# Header (with back button)
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	header.connect("draw", _draw_wechat_header.bind(header))
	box.add_child(header)

	var back_button := Button.new()
	back_button.custom_minimum_size = Vector2(24, 20)
	back_button.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
	back_button.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
	back_button.connect("draw", _draw_back_button.bind(back_button))
	back_button.pressed.connect(_close_task)
	header.add_child(back_button)

	var title := Label.new()
	title.text = "任务看板"
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)

	# Scroll Container
	_task_scroll = ScrollContainer.new()
	_task_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_task_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(_task_scroll)

	# Task List
	_task_list = VBoxContainer.new()
	_task_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_task_list.add_theme_constant_override("separation", 4)
	_task_scroll.add_child(_task_list)

	return margin_container


# Camera App Navigation & Capture Logic
func _open_camera() -> void:
	_home_screen.visible = false
	_chat_screen.visible = false
	_gallery_screen.visible = false
	_photo_preview_screen.visible = false
	_camera_screen.visible = true
	_update_shutter_preview()


func _close_camera() -> void:
	_camera_screen.visible = false
	_home_screen.visible = true


func _take_photo() -> void:
	if _sub_viewport == null:
		return

	# Flash effect: Trigger white screen and fade it
	if _flash_rect != null:
		_flash_rect.color = Color(1.0, 1.0, 1.0, 0.85)
		var tween := create_tween()
		tween.tween_property(_flash_rect, "color", Color(1.0, 1.0, 1.0, 0.0), 0.15)
	
	# Hide phone UI layer for screenshot
	if _phone_layer != null:
		_phone_layer.visible = false
		
	# Wait for rendering to update without the phone UI layer
	await get_tree().process_frame
	await RenderingServer.frame_post_draw
	
	# Capture main viewport
	var img := get_viewport().get_texture().get_image()
	
	# Restore phone UI layer immediately
	if _phone_layer != null:
		_phone_layer.visible = true
		
	if img == null:
		_show_toast("拍照失败！")
		return

	# Crop out the phone UI (which occupies the right 248px of the viewport)
	var crop_w := int(img.get_width() - 248)
	var crop_h := img.get_height()
	if crop_w > 0 and crop_h > 0:
		img = img.get_region(Rect2i(0, 0, crop_w, crop_h))

	# Ensure screenshots directory exists
	DirAccess.make_dir_recursive_absolute("user://screenshots")
	
	var time := Time.get_datetime_dict_from_system()
	var filename := "screenshot_%04d%02d%02d_%02d%02d%02d.png" % [
		time.year, time.month, time.day,
		time.hour, time.minute, time.second
	]
	var path := "user://screenshots/" + filename
	var err := img.save_png(path)
	
	if err == OK:
		_show_toast("照片已保存至相册")
		_update_shutter_preview()
	else:
		_show_toast("保存失败！")


func _get_cropped_thumbnail_texture(img: Image) -> ImageTexture:
	if img == null:
		return null
	
	var w := img.get_width()
	var h := img.get_height()
	var size := int(min(w, h) * 0.5) # Crop 50% of the smaller dimension (zoomed center)
	if size > 0:
		var x := int((w - size) / 2.0)
		var y := int((h - size) / 2.0)
		var cropped := img.get_region(Rect2i(x, y, size, size))
		return ImageTexture.create_from_image(cropped)
		
	return ImageTexture.create_from_image(img)


func _update_shutter_preview() -> void:
	if _shutter_preview_rect == null:
		return
		
	var latest := _get_latest_screenshot_path()
	if not latest.is_empty():
		var img := Image.load_from_file(latest)
		if img != null:
			_shutter_preview_rect.texture = _get_cropped_thumbnail_texture(img)
			return
			
	# Clear if no screenshots
	_shutter_preview_rect.texture = null


func _get_latest_screenshot_path() -> String:
	if not DirAccess.dir_exists_absolute("user://screenshots"):
		return ""
		
	var files := DirAccess.get_files_at("user://screenshots")
	var png_files: Array[String] = []
	for f in files:
		if f.ends_with(".png"):
			png_files.append(f)
			
	if png_files.is_empty():
		return ""
		
	png_files.sort()
	return "user://screenshots/" + png_files[-1]


func _show_toast(message: String) -> void:
	if _phone_layer == null:
		return
		
	var toast := PanelContainer.new()
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.1, 0.1, 0.1, 0.85)
	style.border_color = Color(0.5, 0.5, 0.5)
	style.set_border_width_all(1)
	style.set_corner_radius_all(0)
	style.set_content_margin(SIDE_LEFT, 8)
	style.set_content_margin(SIDE_RIGHT, 8)
	style.set_content_margin(SIDE_TOP, 4)
	style.set_content_margin(SIDE_BOTTOM, 4)
	toast.add_theme_stylebox_override("panel", style)
	
	var label := Label.new()
	label.text = message
	label.add_theme_font_size_override("font_size", theme.speech_font_size)
	label.add_theme_color_override("font_color", Color.WHITE)
	toast.add_child(label)
	
	var screen := _phone_layer.get_node("PhonePanel/PhoneScreen")
	if screen != null:
		screen.add_child(toast)
		toast.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
		toast.grow_vertical = Control.GROW_DIRECTION_BEGIN
		toast.offset_bottom = -40
		
		# Auto free
		await get_tree().create_timer(1.5).timeout
		if is_instance_valid(toast):
			toast.queue_free()


# Gallery App Logic
func _open_gallery() -> void:
	_home_screen.visible = false
	_chat_screen.visible = false
	_camera_screen.visible = false
	_photo_preview_screen.visible = false
	_gallery_screen.visible = true
	_refresh_gallery_grid()


func _close_gallery() -> void:
	_gallery_screen.visible = false
	_home_screen.visible = true


func _refresh_gallery_grid() -> void:
	if _gallery_grid == null:
		return
		
	for child in _gallery_grid.get_children():
		child.queue_free()
		
	if not DirAccess.dir_exists_absolute("user://screenshots"):
		_show_gallery_empty()
		return
		
	var files := DirAccess.get_files_at("user://screenshots")
	var png_files: Array[String] = []
	for f in files:
		if f.ends_with(".png"):
			png_files.append(f)
			
	# Newest first
	png_files.reverse()
	
	if png_files.is_empty():
		_show_gallery_empty()
		return
		
	for file_name in png_files:
		var path := "user://screenshots/" + file_name
		
		var photo_btn := Button.new()
		photo_btn.custom_minimum_size = Vector2(66, 44)
		photo_btn.add_theme_stylebox_override("normal", StyleBoxEmpty.new())
		photo_btn.add_theme_stylebox_override("hover", StyleBoxEmpty.new())
		photo_btn.add_theme_stylebox_override("pressed", StyleBoxEmpty.new())
		photo_btn.pressed.connect(_open_photo_preview.bind(path))
		
		var thumb := TextureRect.new()
		thumb.set_anchors_preset(Control.PRESET_FULL_RECT)
		thumb.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		thumb.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
		thumb.texture_filter = Control.TEXTURE_FILTER_NEAREST
		thumb.mouse_filter = Control.MOUSE_FILTER_IGNORE
		
		var img := Image.load_from_file(path)
		if img != null:
			thumb.texture = _get_cropped_thumbnail_texture(img)
			
		photo_btn.add_child(thumb)
		_gallery_grid.add_child(photo_btn)


func _show_gallery_empty() -> void:
	var empty_lbl := Label.new()
	empty_lbl.text = "暂无照片"
	empty_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	empty_lbl.add_theme_font_size_override("font_size", theme.detail_font_size)
	empty_lbl.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
	empty_lbl.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_gallery_grid.add_child(empty_lbl)


# Photo Preview Logic
func _open_photo_preview(path: String) -> void:
	_current_photo_path = path
	var img := Image.load_from_file(path)
	if img != null:
		var tex := ImageTexture.create_from_image(img)
		_preview_texture_rect.texture = tex
	_gallery_screen.visible = false
	_photo_preview_screen.visible = true


func _close_photo_preview() -> void:
	_photo_preview_screen.visible = false
	_gallery_screen.visible = true
	_refresh_gallery_grid()


func _delete_current_photo() -> void:
	if _current_photo_path.is_empty():
		return
		
	var err := DirAccess.remove_absolute(_current_photo_path)
	if err == OK:
		_show_toast("已删除照片")
	else:
		_show_toast("删除失败")
		
	_current_photo_path = ""
	_close_photo_preview()


func _open_task() -> void:
	_home_screen.visible = false
	_chat_screen.visible = false
	_camera_screen.visible = false
	_gallery_screen.visible = false
	_photo_preview_screen.visible = false
	_task_screen.visible = true
	_refresh_task_list()


func _close_task() -> void:
	_task_screen.visible = false
	_home_screen.visible = true


func _refresh_task_list() -> void:
	if _task_list == null:
		return

	# 清空旧内容
	for child in _task_list.get_children():
		child.queue_free()

	# ====== 顶部：PRD 总结卡片（如果有）======
	if not _prd_summary.is_empty():
		_add_prd_card()

	# 显示空状态
	if _task_data.is_empty() and _prd_summary.is_empty():
		var empty_lbl := Label.new()
		empty_lbl.text = "暂无任务，通过会议分配任务"
		empty_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		empty_lbl.add_theme_font_size_override("font_size", theme.detail_font_size)
		empty_lbl.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
		empty_lbl.size_flags_vertical = Control.SIZE_EXPAND_FILL
		_task_list.add_child(empty_lbl)
		return
	
	# 按 assignee 分组显示
	var sorted_tasks := _task_data.duplicate()
	sorted_tasks.sort_custom(func(a, b): return int(a.get("priority", 0)) > int(b.get("priority", 0)))
	
	for task in sorted_tasks:
		_add_task_card(task)


## 添加 PRD 总结卡片（显示在任务列表顶部）
func _add_prd_card() -> void:
	var card := PanelContainer.new()
	var card_style := StyleBoxFlat.new()
	card_style.bg_color = Color(0.97, 0.94, 0.86)  # 浅暖黄色背景
	card_style.border_color = Color(0.85, 0.72, 0.30)  # 金色边框
	card_style.set_border_width_all(1)
	card_style.set_corner_radius_all(4)
	card_style.set_content_margin(SIDE_LEFT, 10)
	card_style.set_content_margin(SIDE_RIGHT, 10)
	card_style.set_content_margin(SIDE_TOP, 8)
	card_style.set_content_margin(SIDE_BOTTOM, 8)
	card.add_theme_stylebox_override("panel", card_style)
	_task_list.add_child(card)

	var inner := VBoxContainer.new()
	inner.add_theme_constant_override("separation", 4)
	card.add_child(inner)

	# 标题行
	var header := HBoxContainer.new()
	inner.add_child(header)

	var header_hbox := HBoxContainer.new()
	inner.add_child(header_hbox)

	var title_lbl := Label.new()
	title_lbl.text = "📋 会议PRD — %s" % _meeting_topic
	title_lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	title_lbl.add_theme_font_size_override("font_size", 12)
	title_lbl.add_theme_color_override("font_color", Color(0.18, 0.18, 0.18))
	title_lbl.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header_hbox.add_child(title_lbl)

	# 复制按钮
	var copy_btn := Button.new()
	copy_btn.text = "复制"
	copy_btn.custom_minimum_size = Vector2(42, 22)
	copy_btn.add_theme_font_size_override("font_size", 10)
	copy_btn.pressed.connect(_copy_prd_to_clipboard)
	header_hbox.add_child(copy_btn)

	# PRD 内容（可滚动文本）
	var prd_label := RichTextLabel.new()
	prd_label.bbcode_enabled = true
	prd_label.fit_content = true
	prd_label.scroll_active = false
	# 将 Markdown 简单转换为 BBCode
	var prd_text := _prd_summary
	prd_text = prd_text.replace("\n## ", "\n\n[b]")
	prd_text = prd_text.replace("## ", "[b]")
	prd_text += "[/b]"
	prd_text = prd_text.replace("- ", "• ")
	prd_label.text = prd_text
	prd_label.add_theme_font_size_override("normal_font_size", 10)
	prd_label.add_theme_color_override("default_color", Color(0.10, 0.10, 0.10))
	prd_label.add_theme_color_override("font_color", Color(0.10, 0.10, 0.10))
	prd_label.size_flags_vertical = Control.SIZE_EXPAND_FILL

	inner.add_child(prd_label)


func _copy_prd_to_clipboard() -> void:
	DisplayServer.clipboard_set(_prd_summary)
	_show_toast("PRD 已复制到剪贴板")


func _add_task_card(task_data: Dictionary) -> void:
	var card := PanelContainer.new()
	var card_style := StyleBoxFlat.new()
	card_style.bg_color = Color(1.0, 1.0, 1.0)
	card_style.border_color = Color(0.88, 0.88, 0.88)
	card_style.set_border_width_all(1)
	card_style.set_corner_radius_all(0)
	card_style.set_content_margin(SIDE_LEFT, 8)
	card_style.set_content_margin(SIDE_RIGHT, 8)
	card_style.set_content_margin(SIDE_TOP, 6)
	card_style.set_content_margin(SIDE_BOTTOM, 6)
	card.add_theme_stylebox_override("panel", card_style)
	_task_list.add_child(card)
	
	var inner := VBoxContainer.new()
	inner.add_theme_constant_override("separation", 2)
	card.add_child(inner)
	
	# Row 1: Title + Status badge
	var row1 := HBoxContainer.new()
	row1.add_theme_constant_override("separation", 6)
	inner.add_child(row1)
	
	var title_lbl := Label.new()
	title_lbl.text = str(task_data.get("title", "无标题"))
	title_lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	title_lbl.add_theme_font_size_override("font_size", theme.detail_font_size)
	title_lbl.add_theme_color_override("font_color", Color(0.12, 0.12, 0.12))
	title_lbl.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row1.add_child(title_lbl)
	
	# Status badge
	var status_text := str(task_data.get("status", "?"))
	var status_color := Color(0.6, 0.6, 6.6)
	match status_text:
		"doing":
			status_color = Color(0.20, 0.55, 0.98)  # Blue for working
		"done":
			status_color = Color(0.18, 0.72, 0.28)  # Green for done
		"review":
			status_color = Color(0.95, 0.62, 0.10)  # Orange for review
		"todo":
			status_color = Color(0.55, 0.55, 0.55)   # Gray for todo
	
	var status_badge := Label.new()
	status_badge.text = _status_label_text(status_text)
	status_badge.add_theme_font_size_override("font_size", theme.detail_font_size - 2)
	status_badge.add_theme_color_override("font_color", Color.WHITE)
	var badge_style := StyleBoxFlat.new()
	badge_style.bg_color = status_color
	badge_style.set_corner_radius_all(2)
	badge_style.set_content_margin(SIDE_LEFT, 4)
	badge_style.set_content_margin(SIDE_RIGHT, 4)
	badge_style.set_content_margin(SIDE_TOP, 1)
	badge_style.set_content_margin(SIDE_BOTTOM, 1)
	status_badge.add_theme_stylebox_override("normal", badge_style)
	row1.add_child(status_badge)
	
	# Row 2: Assignee + Type + Progress bar
	var row2 := HBoxContainer.new()
	row2.add_theme_constant_override("separation", 6)
	inner.add_child(row2)
	
	var assignee_name := str(task_data.get("assignee_id", ""))
	if _worker_profiles.has(assignee_name):
		assignee_name = str(_worker_profiles[assignee_name].get("name", assignee_name))
	
	var info_lbl := Label.new()
	info_lbl.text = "%s · %s" % [assignee_name, str(task_data.get("task_type", ""))]
	info_lbl.add_theme_font_size_override("font_size", theme.detail_font_size - 1)
	info_lbl.add_theme_color_override("font_color", Color(0.50, 0.50, 0.50))
	row2.add_child(info_lbl)
	
	# Progress percentage
	var progress := float(task_data.get("progress", 0.0))
	var prog_lbl := Label.new()
	prog_lbl.text = "%.0f%%" % (progress * 100.0)
	prog_lbl.add_theme_font_size_override("font_size", theme.detail_font_size - 1)
	prog_lbl.add_theme_color_override("font_color", status_color)
	row2.add_child(prog_lbl)
	
	# Progress bar
	var prog_bar := Control.new()
	prog_bar.custom_minimum_size = Vector2(0, 4)
	prog_bar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	prog_bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	prog_bar.connect("draw", _draw_progress_bar.bind(prog_bar, progress, status_color))
	inner.add_child(prog_bar)


func _status_label_text(status: String) -> String:
	match status:
		"doing": return "进行中"
		"done": return "已完成"
		"review": return "待审核"
		"todo": return "待认领"
		_: return status


func _draw_progress_bar(bar: Control, progress: float, color: Color) -> void:
	var size := bar.size
	# Background
	bar.draw_rect(Rect2(0, 0, size.x, size.y), Color(0.90, 0.90, 0.90))
	# Fill
	if progress > 0.0:
		bar.draw_rect(Rect2(0, 0, size.x * clampf(progress, 0.0, 1.0), size.y), color)


func _draw_task_logo(canvas: Control) -> void:
	var border_color := Color(0.12, 0.12, 0.12)
	var bg_color := Color(0.85, 0.55, 0.18)  # Amber/gold color for tasks
	
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(0.0, 0.0, 32.0, 32.0), border_color)
	_styles.draw_pixel_box_with_cut_corners(canvas, Rect2(1.0, 1.0, 30.0, 30.0), bg_color)
	
	# Clipboard/checklist icon
	var check_color := Color(1.0, 1.0, 1.0)
	canvas.draw_rect(Rect2(7, 6, 18, 22), check_color)
	canvas.draw_rect(Rect2(8, 7, 16, 20), border_color)
	# Check lines
	canvas.draw_rect(Rect2(11, 12, 8, 2), border_color)
	canvas.draw_rect(Rect2(11, 17, 6, 2), border_color)
	canvas.draw_rect(Rect2(11, 22, 10, 2), border_color)


## 更新任务数据（由 office_demo 收到 task_update 命令后调用）
func update_task_data(payload: Variant) -> void:
	if payload is Dictionary:
		var dict := payload as Dictionary
		if dict.has("tasks") and dict["tasks"] is Array:
			_task_data = dict["tasks"] as Array
		if dict.has("prd_summary"):
			_prd_summary = str(dict.get("prd_summary", ""))
		if dict.has("meeting_topic"):
			_meeting_topic = str(dict.get("meeting_topic", ""))
	elif payload is Array:
		_task_data = payload as Array
	# 如果任务面板正在显示，刷新列表
	if _task_screen != null and _task_screen.visible:
		_refresh_task_list()


func clear_tasks() -> void:
	_task_data = []
	_prd_summary = ""
	_meeting_topic = ""
	if _task_screen != null and _task_screen.visible:
		_refresh_task_list()
