class_name PixelUiTheme
extends Resource

## 程序化像素 UI 参数。
## 所有面板、输入框、按钮都从这些参数生成，避免在场景脚本里散落硬编码。

@export_category("布局")
@export var boss_panel_position: Vector2 = Vector2(12, 10)
@export var boss_panel_size: Vector2 = Vector2(360, 76)
@export var boss_panel_margin_bottom: float = 10.0
@export var token_bar_size: Vector2 = Vector2(300, 22)
@export var token_bar_margin_bottom: float = 8.0
@export var detail_panel_size: Vector2 = Vector2(330, 210)
@export var detail_panel_offset: Vector2 = Vector2(14, 14)
@export var speech_bubble_offset: Vector2 = Vector2(-46, -46)
@export var speech_bubble_size: Vector2 = Vector2(94, 34)
@export var speech_bubble_max_height: float = 58.0
@export var status_bubble_offset: Vector2 = Vector2(-26, -64)
@export var status_bubble_min_size: Vector2 = Vector2(52, 12)
@export var viewport_padding: float = 8.0
@export var row_separation: int = 6

@export_category("文字")
@export var ui_font_size: int = 12
@export var detail_font_size: int = 10
@export var speech_font_size: int = 8
@export var status_font_size: int = 7
@export var text_color: Color = Color(0.95, 0.96, 0.88)
@export var detail_text_color: Color = Color(0.93, 0.95, 0.88)
@export var placeholder_color: Color = Color(0.62, 0.63, 0.58)

@export_category("程序化面板")
@export var panel_bg_color: Color = Color(0.07, 0.08, 0.10, 0.92)
@export var panel_border_color: Color = Color(0.82, 0.78, 0.58)
@export var panel_border_width: int = 2
@export var panel_margin_left: float = 8.0
@export var panel_margin_top: float = 6.0
@export var panel_margin_right: float = 8.0
@export var panel_margin_bottom: float = 6.0

@export_category("头顶气泡")
@export var speech_bg_color: Color = Color(0.98, 0.96, 0.82, 0.96)
@export var speech_border_color: Color = Color(0.22, 0.20, 0.18, 1.0)
@export var speech_text_color: Color = Color(0.12, 0.11, 0.10, 1.0)
@export var speech_border_width: int = 1
@export var speech_type_chars_per_second: float = 18.0
@export var speech_hold_seconds: float = 2.8
@export var thinking_dot_seconds: float = 0.28
@export var speech_corner_radius: int = 6

@export_category("常驻状态小气泡")
@export var status_bg_color: Color = Color(0.10, 0.11, 0.14, 0.82)
@export var status_border_color: Color = Color(0.55, 0.52, 0.40, 0.9)
@export var status_text_color: Color = Color(0.92, 0.93, 0.86, 1.0)
@export var status_border_width: int = 1
@export var status_corner_radius: int = 5

@export_category("程序化输入框")
@export var field_bg_color: Color = Color(0.10, 0.11, 0.13, 0.96)
@export var field_focus_bg_color: Color = Color(0.12, 0.13, 0.15, 0.98)
@export var field_border_color: Color = Color(0.48, 0.48, 0.42)
@export var field_focus_border_color: Color = Color(0.90, 0.82, 0.42)
@export var field_border_width: int = 1
@export var field_focus_border_width: int = 2

@export_category("程序化按钮")
@export var button_bg_color: Color = Color(0.15, 0.16, 0.18, 1.0)
@export var button_hover_bg_color: Color = Color(0.20, 0.20, 0.22, 1.0)
@export var button_pressed_bg_color: Color = Color(0.08, 0.09, 0.10, 1.0)
@export var button_border_color: Color = Color(0.64, 0.58, 0.36)
@export var button_active_border_color: Color = Color(0.92, 0.80, 0.42)

@export_category("SDF 风格控制")
@export var corner_radius: int = 0
@export var content_margin_left: float = 6.0
@export var content_margin_top: float = 4.0
@export var content_margin_right: float = 6.0
@export var content_margin_bottom: float = 4.0
