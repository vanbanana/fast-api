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
const WORKER_BUBBLE := Color(0.92, 0.93, 0.90, 1.0)
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


func setup(ui_theme: PixelUiTheme, worker_profiles: Dictionary) -> void:
	theme = ui_theme
	_styles = PixelStyles.new(theme)
	_worker_profiles = worker_profiles

	var layer := CanvasLayer.new()
	layer.name = "PhoneLayer"
	add_child(layer)

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
	var shell_style := _styles.make_pixel_box(Color(0.05, 0.05, 0.07, 1.0), Color(0.70, 0.68, 0.55, 1.0), 3)
	shell_style.set_corner_radius_all(10)
	phone.add_theme_stylebox_override("panel", shell_style)
	layer.add_child(phone)

	var screen := PanelContainer.new()
	screen.name = "PhoneScreen"
	var screen_style := _styles.make_pixel_box(SCREEN_BG, Color(0.25, 0.26, 0.30, 1.0), 1)
	screen_style.set_corner_radius_all(6)
	screen.add_theme_stylebox_override("panel", screen_style)
	phone.add_child(screen)

	_home_screen = _build_home_screen()
	screen.add_child(_home_screen)
	_chat_screen = _build_chat_screen()
	_chat_screen.visible = false
	screen.add_child(_chat_screen)
	_mention_popup = _build_mention_popup()
	_mention_popup.visible = false
	screen.add_child(_mention_popup)


func _build_home_screen() -> Control:
	var box := VBoxContainer.new()
	box.name = "HomeScreen"
	box.add_theme_constant_override("separation", 8)

	_clock_label = Label.new()
	_clock_label.text = "办公室"
	_clock_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_clock_label.add_theme_font_size_override("font_size", theme.ui_font_size)
	_clock_label.add_theme_color_override("font_color", theme.text_color)
	box.add_child(_clock_label)

	var grid := GridContainer.new()
	grid.columns = 3
	grid.add_theme_constant_override("h_separation", 10)
	grid.add_theme_constant_override("v_separation", 10)
	box.add_child(grid)

	var wechat_button := Button.new()
	wechat_button.text = "微信"
	wechat_button.custom_minimum_size = Vector2(58, 52)
	wechat_button.add_theme_font_size_override("font_size", theme.ui_font_size)
	wechat_button.add_theme_color_override("font_color", Color.WHITE)
	var wechat_style := _styles.make_pixel_box(WECHAT_GREEN, Color(0.10, 0.40, 0.18, 1.0), 2)
	wechat_style.set_corner_radius_all(8)
	wechat_button.add_theme_stylebox_override("normal", wechat_style)
	wechat_button.add_theme_stylebox_override("hover", wechat_style)
	wechat_button.add_theme_stylebox_override("pressed", wechat_style)
	wechat_button.pressed.connect(_open_chat)
	grid.add_child(wechat_button)

	_status_label = Label.new()
	_status_label.text = "等待后端连接"
	_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	_status_label.add_theme_color_override("font_color", theme.placeholder_color)
	_status_label.size_flags_vertical = Control.SIZE_SHRINK_END
	box.add_child(_status_label)
	return box


func _build_chat_screen() -> Control:
	var box := VBoxContainer.new()
	box.name = "ChatScreen"
	box.add_theme_constant_override("separation", 4)

	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	box.add_child(header)

	var back_button := Button.new()
	back_button.text = "<"
	back_button.custom_minimum_size = Vector2(24, 20)
	_styles.apply_button_style(back_button)
	back_button.pressed.connect(_close_chat)
	header.add_child(back_button)

	var title := Label.new()
	title.text = "公司工作群 (%d)" % (_worker_profiles.size() + 1)
	title.add_theme_font_size_override("font_size", theme.ui_font_size)
	title.add_theme_color_override("font_color", theme.text_color)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)

	_message_scroll = ScrollContainer.new()
	_message_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_message_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	box.add_child(_message_scroll)

	_message_list = VBoxContainer.new()
	_message_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_message_list.add_theme_constant_override("separation", 5)
	_message_scroll.add_child(_message_list)

	var input_row := HBoxContainer.new()
	input_row.add_theme_constant_override("separation", 4)
	box.add_child(input_row)

	var mention_button := Button.new()
	mention_button.text = "@"
	mention_button.custom_minimum_size = Vector2(24, 20)
	_styles.apply_button_style(mention_button)
	mention_button.pressed.connect(_toggle_mention_popup)
	input_row.add_child(mention_button)

	_chat_input = LineEdit.new()
	_chat_input.placeholder_text = "发消息给项目组…"
	_chat_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_input.text_submitted.connect(_on_text_submitted)
	_styles.apply_line_edit_style(_chat_input)
	input_row.add_child(_chat_input)

	var send_button := Button.new()
	send_button.text = "发送"
	_styles.apply_button_style(send_button)
	send_button.pressed.connect(_submit)
	input_row.add_child(send_button)
	return box


func _build_mention_popup() -> PanelContainer:
	var popup := PanelContainer.new()
	popup.name = "MentionPopup"
	popup.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	popup.offset_bottom = -26.0
	popup.grow_vertical = Control.GROW_DIRECTION_BEGIN
	var style := _styles.make_pixel_box(Color(0.12, 0.13, 0.16, 0.98), theme.panel_border_color, 1)
	style.set_corner_radius_all(6)
	popup.add_theme_stylebox_override("panel", style)

	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(0, 150)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	popup.add_child(scroll)

	var list := VBoxContainer.new()
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	list.add_theme_constant_override("separation", 2)
	scroll.add_child(list)

	for worker_id in _worker_profiles.keys():
		var profile: Dictionary = _worker_profiles[worker_id]
		var item := Button.new()
		item.text = "%s · %s" % [str(profile.get("name", worker_id)), str(profile.get("role", ""))]
		item.alignment = HORIZONTAL_ALIGNMENT_LEFT
		item.add_theme_font_size_override("font_size", theme.detail_font_size)
		item.add_theme_color_override("font_color", MENTION_COLOR)
		item.flat = true
		item.pressed.connect(_pick_mention.bind(str(worker_id)))
		list.add_child(item)
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


func _resolve_mentions(text: String) -> Array[String]:
	# 以文本里真实存在的 @名字 为准，避免删掉 @ 后还残留目标。
	var resolved: Array[String] = []
	for worker_id in _worker_profiles.keys():
		var profile: Dictionary = _worker_profiles[worker_id]
		if text.contains("@%s" % str(profile.get("name", ""))):
			resolved.append(str(worker_id))
	return resolved


func add_chat_message(sender_name: String, text: String, is_boss: bool = false) -> void:
	if _message_list == null or text.strip_edges().is_empty():
		return
	var entry := VBoxContainer.new()
	entry.add_theme_constant_override("separation", 1)

	var name_label := Label.new()
	name_label.text = sender_name
	name_label.add_theme_font_size_override("font_size", theme.detail_font_size)
	name_label.add_theme_color_override("font_color", theme.placeholder_color)
	if is_boss:
		name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	entry.add_child(name_label)

	var bubble_row := HBoxContainer.new()
	entry.add_child(bubble_row)
	if is_boss:
		var spacer := Control.new()
		spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		bubble_row.add_child(spacer)

	var bubble := PanelContainer.new()
	var bubble_style := _styles.make_pixel_box(BOSS_BUBBLE if is_boss else WORKER_BUBBLE, Color(0.20, 0.20, 0.20, 0.6), 1)
	bubble_style.set_corner_radius_all(6)
	bubble.add_theme_stylebox_override("panel", bubble_style)
	bubble_row.add_child(bubble)

	var label := Label.new()
	label.text = text.substr(0, 220)
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.custom_minimum_size = Vector2(0, 0)
	label.add_theme_font_size_override("font_size", theme.detail_font_size)
	label.add_theme_color_override("font_color", BUBBLE_TEXT)
	bubble.add_child(label)
	bubble.custom_minimum_size = Vector2(minf(160.0, float(text.length()) * float(theme.detail_font_size)), 0)

	_message_list.add_child(entry)
	if _message_list.get_child_count() > 60:
		_message_list.get_child(0).queue_free()
	_scroll_messages_to_bottom()


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
