class_name DemoWorker
extends CharacterBody2D

signal target_reached(target: Marker2D)
signal decision_requested()

@export_category("移动参数")
@export var walk_speed: float = 48.0
@export var arrive_distance: float = 3.0
@export var min_wait_time: float = 1.2
@export var max_wait_time: float = 3.0
@export var use_avoidance: bool = false
@export var repath_when_stuck_time: float = 1.2

@export_category("角色资源")
@export var character_texture: Texture2D

@export_category("目标来源")
@export var seat_marker_group: StringName = &"seat_markers"
@export var idle_marker_group: StringName = &"idle_markers"
@export var roam_marker_group: StringName = &"supMarkers"
@export var worker_group: StringName = &"demo_workers"
@export var start_marker: Marker2D
@export var seated_offset: Vector2 = Vector2.ZERO
@export var visit_offset: Vector2 = Vector2(0, 18)
@export var visit_slot_spacing: float = 9.0
@export var visit_slot_count: int = 4

@export_category("软分离")
@export var separation_enabled: bool = true
@export var separation_radius: float = 10.0
@export var separation_strength: float = 22.0

@export_category("动画名")
@export var walk_animation: StringName = &"walk"
@export var idle_animation: StringName = &"idle"
@export var seat_down_animation: StringName = &"desk_down"
@export var seat_up_animation: StringName = &"desk_up"
@export var seat_left_animation: StringName = &"desk_left"
@export var seat_right_animation: StringName = &"desk_right"

@onready var sprite: Sprite2D = $Sprite2D
@onready var animation_player: AnimationPlayer = $AnimationPlayer
@onready var navigation_agent: NavigationAgent2D = $NavigationAgent2D
@onready var wait_timer: Timer = $WaitTimer

var current_target: Marker2D
var current_target_is_seat: bool = false
var next_target_is_seat: bool = true
var reserved_seat: Marker2D
var rng := RandomNumberGenerator.new()
var last_position: Vector2
var stuck_time: float = 0.0
var default_z_index: int = 0
var external_decision_enabled: bool = false
var target_position_override: Vector2 = Vector2.ZERO
var has_target_position_override: bool = false
var reserved_visit_marker: Marker2D
var reserved_visit_slot: int = -1


func _ready() -> void:
	rng.randomize()
	navigation_agent.target_desired_distance = arrive_distance
	navigation_agent.path_desired_distance = arrive_distance
	navigation_agent.avoidance_enabled = use_avoidance
	if use_avoidance:
		navigation_agent.velocity_computed.connect(_on_velocity_computed)
	wait_timer.timeout.connect(_on_wait_timer_timeout)

	if character_texture:
		sprite.texture = character_texture

	# 起点也由场景里的 Marker 管理，便于在编辑器拖拽调整。
	if start_marker:
		global_position = start_marker.global_position
	last_position = global_position
	default_z_index = z_index

	call_deferred("_start_after_navigation_sync")


func _start_after_navigation_sync() -> void:
	# 等待导航地图完成同步，避免开场第一帧拿到空路径后原地播放走路。
	await get_tree().physics_frame
	if external_decision_enabled:
		wait_timer.start(rng.randf_range(0.2, 0.5))
		return
	_choose_next_target()


func _physics_process(delta: float) -> void:
	if current_target == null:
		return

	if navigation_agent.is_navigation_finished():
		_arrive_at_target()
		return
	if global_position.distance_to(_get_target_position()) <= arrive_distance:
		_arrive_at_target()
		return

	var next_position := navigation_agent.get_next_path_position()
	if global_position.distance_to(next_position) <= 0.5:
		next_position = _get_target_position()

	var direction := global_position.direction_to(next_position)
	if direction.is_zero_approx():
		velocity = Vector2.ZERO
		move_and_slide()
		return

	sprite.flip_h = direction.x < 0

	var next_velocity := direction * walk_speed + _get_separation_velocity()
	if use_avoidance:
		navigation_agent.velocity = next_velocity
	else:
		velocity = next_velocity
		move_and_slide()
	_update_stuck_state(delta)


func _choose_next_target() -> void:
	# 离开座位进入移动状态时恢复默认层级。
	z_index = default_z_index

	# 离开座位前释放占用，其他角色才能重新选择这个椅子。
	if !next_target_is_seat:
		_release_reserved_seat()

	var target_pool := _collect_available_markers(seat_marker_group if next_target_is_seat else idle_marker_group)
	if target_pool.is_empty():
		_play_idle()
		wait_timer.start(rng.randf_range(min_wait_time, max_wait_time))
		return

	current_target = target_pool[rng.randi_range(0, target_pool.size() - 1)]
	current_target_is_seat = next_target_is_seat
	next_target_is_seat = !next_target_is_seat

	if current_target_is_seat:
		_reserve_seat(current_target)

	has_target_position_override = false
	navigation_agent.target_position = _get_target_position()
	stuck_time = 0.0
	last_position = global_position
	animation_player.play(walk_animation)


func move_to_marker_id(target_id: StringName) -> bool:
	# 给后端 agent 使用的指令入口：后端只传 Marker 名称，具体目标仍从场景分组里查找。
	var marker := _find_marker_by_id(target_id)
	if marker == null:
		return false

	if _is_other_worker_desk(marker):
		return visit_marker_id(target_id)
	if !_is_marker_available(marker):
		return false

	wait_timer.stop()
	_begin_target(marker, marker.is_in_group(seat_marker_group))
	return true


func force_seat_marker_id(target_id: StringName) -> bool:
	# 会议调度已经在后端分配好唯一座位；这里清掉旧占用并强制按座位逻辑入座。
	var marker := _find_marker_by_id(target_id)
	if marker == null or !marker.is_in_group(seat_marker_group):
		return false

	if marker.has_meta("claimed_by"):
		marker.remove_meta("claimed_by")
	wait_timer.stop()
	has_target_position_override = false
	_begin_target(marker, true)
	return true


func visit_marker_id(target_id: StringName) -> bool:
	# 找同事时只把对方工位当定位点，站在旁边沟通，不占座、不坐下。
	var marker := _find_marker_by_id(target_id)
	if marker == null:
		return false

	wait_timer.stop()
	_reserve_visit_slot(marker)
	target_position_override = marker.global_position + _get_visit_offset(marker, reserved_visit_slot)
	has_target_position_override = true
	_begin_target(marker, false)
	return true


func set_external_decision_enabled(enabled: bool) -> void:
	# 后端连接成功后由 OfficeDemo 开启，等待结束时改为请求 agent 决策。
	external_decision_enabled = enabled
	if enabled:
		velocity = Vector2.ZERO
		current_target = null
		has_target_position_override = false
		_release_reserved_seat()
		_release_reserved_visit_slot()
		_play_idle()
		wait_timer.start(rng.randf_range(0.2, 0.5))


func wait_for_next_decision() -> void:
	# 后端返回 idle 时使用，避免员工卡住不再进入自主循环。
	_play_idle()
	wait_timer.start(rng.randf_range(min_wait_time, max_wait_time))


func _begin_target(marker: Marker2D, is_seat: bool) -> void:
	# 随机行为和后端行为共用同一套进入目标逻辑，避免两边维护不同规则。
	z_index = default_z_index
	_release_reserved_seat()
	if !has_target_position_override:
		_release_reserved_visit_slot()

	current_target = marker
	current_target_is_seat = is_seat
	next_target_is_seat = !is_seat
	if !has_target_position_override:
		target_position_override = Vector2.ZERO

	if current_target_is_seat:
		_reserve_seat(current_target)

	navigation_agent.target_position = _get_target_position()
	stuck_time = 0.0
	last_position = global_position
	animation_player.play(walk_animation)


func _arrive_at_target() -> void:
	if current_target == null:
		return

	velocity = Vector2.ZERO
	move_and_slide()

	if current_target_is_seat:
		global_position = _get_seated_position(current_target)
		_apply_seat_sorting(current_target)
		_play_seat_animation(current_target)
	else:
		if has_target_position_override:
			global_position = target_position_override
		_play_idle()

	target_reached.emit(current_target)
	current_target = null
	has_target_position_override = false
	stuck_time = 0.0
	last_position = global_position
	wait_timer.start(rng.randf_range(min_wait_time, max_wait_time))


func _get_separation_velocity() -> Vector2:
	if !separation_enabled:
		return Vector2.ZERO

	var push := Vector2.ZERO
	for node in get_tree().get_nodes_in_group(worker_group):
		if node == self or !(node is Node2D):
			continue

		var other_worker: Node2D = node as Node2D
		var offset: Vector2 = global_position - other_worker.global_position
		var distance: float = offset.length()
		if distance <= 0.001 or distance >= separation_radius:
			continue

		var weight: float = 1.0 - distance / separation_radius
		push += offset.normalized() * weight

	if push.is_zero_approx():
		return Vector2.ZERO
	return push.normalized() * separation_strength


func _get_seated_position(marker: Marker2D) -> Vector2:
	# 每把椅子可以通过元数据单独微调坐下落点，避免会议室和工位共用一个偏移。
	var marker_offset: Vector2 = seated_offset
	if marker.has_meta("seat_offset"):
		var raw_offset: Variant = marker.get_meta("seat_offset")
		if raw_offset is Vector2:
			marker_offset = raw_offset as Vector2
	return marker.global_position + marker_offset


func _get_visit_offset(marker: Marker2D, slot_index: int = 0) -> Vector2:
	# 拜访目标依据座位朝向选站位，避免站到对方椅子中心。
	var direction := StringName(str(marker.get_meta("seat_direction", "down")))
	var slot_offset := _get_visit_slot_offset(direction, slot_index)
	match direction:
		&"up":
			return Vector2(0, -absf(visit_offset.y)) + slot_offset
		&"left":
			return Vector2(-absf(visit_offset.y), 0) + slot_offset
		&"right":
			return Vector2(absf(visit_offset.y), 0) + slot_offset
		_:
			return Vector2(0, absf(visit_offset.y)) + slot_offset


func _get_visit_slot_offset(direction: StringName, slot_index: int) -> Vector2:
	var clamped_slot := clampi(slot_index, 0, maxi(0, visit_slot_count - 1))
	var centered := float(clamped_slot) - float(maxi(1, visit_slot_count) - 1) * 0.5
	var amount := centered * visit_slot_spacing
	match direction:
		&"left", &"right":
			return Vector2(0, amount)
		_:
			return Vector2(amount, 0)


func _get_target_position() -> Vector2:
	if has_target_position_override:
		return target_position_override
	if current_target == null:
		return global_position
	return current_target.global_position


func _apply_seat_sorting(marker: Marker2D) -> void:
	# 原工程主要靠 y-sort；会议室横向椅子额外用元数据声明坐下后压在椅子上层。
	if !marker.has_meta("seat_z_index"):
		z_index = default_z_index
		return

	var raw_z_index: Variant = marker.get_meta("seat_z_index")
	if raw_z_index is int:
		z_index = int(raw_z_index)
	elif raw_z_index is float:
		z_index = int(raw_z_index)


func _update_stuck_state(delta: float) -> void:
	# 如果路径还没到，但角色长时间几乎没位移，就重新抽一个目标避免堵死。
	if global_position.distance_to(last_position) < 0.25:
		stuck_time += delta
	else:
		stuck_time = 0.0
		last_position = global_position

	if stuck_time < repath_when_stuck_time:
		return

	if current_target_is_seat:
		_release_reserved_seat()
	_release_reserved_visit_slot()
	current_target = null
	stuck_time = 0.0
	_play_idle()
	wait_timer.start(rng.randf_range(0.2, 0.6))


func _collect_available_markers(group_name: StringName) -> Array[Marker2D]:
	var markers: Array[Marker2D] = []
	for node in get_tree().get_nodes_in_group(group_name):
		if node is Marker2D and _is_marker_available(node):
			markers.append(node)
	return markers


func _find_marker_by_id(target_id: StringName) -> Marker2D:
	var group_names: Array[StringName] = [seat_marker_group, idle_marker_group, roam_marker_group]
	for group_name in group_names:
		for node in get_tree().get_nodes_in_group(group_name):
			if node is Marker2D and node.name == target_id:
				return node
	return null


func _is_marker_available(marker: Marker2D) -> bool:
	if !marker.has_meta("claimed_by"):
		return true
	return int(marker.get_meta("claimed_by")) == get_instance_id()


func _is_other_worker_desk(marker: Marker2D) -> bool:
	var marker_name := str(marker.name)
	if !marker_name.begins_with("worker") or !marker_name.ends_with("Marker"):
		return false
	return marker_name != "%sMarker" % str(name)


func _reserve_seat(marker: Marker2D) -> void:
	reserved_seat = marker
	reserved_seat.set_meta("claimed_by", get_instance_id())


func _release_reserved_seat() -> void:
	if reserved_seat and reserved_seat.has_meta("claimed_by"):
		if int(reserved_seat.get_meta("claimed_by")) == get_instance_id():
			reserved_seat.remove_meta("claimed_by")
	reserved_seat = null


func _reserve_visit_slot(marker: Marker2D) -> void:
	_release_reserved_visit_slot()
	var claims := _get_visit_claims(marker)
	var slot := _first_free_visit_slot(claims)
	claims[str(slot)] = get_instance_id()
	marker.set_meta("visit_claims", claims)
	reserved_visit_marker = marker
	reserved_visit_slot = slot


func _release_reserved_visit_slot() -> void:
	if reserved_visit_marker == null or reserved_visit_slot < 0:
		reserved_visit_marker = null
		reserved_visit_slot = -1
		return
	var claims := _get_visit_claims(reserved_visit_marker)
	var key := str(reserved_visit_slot)
	if int(claims.get(key, 0)) == get_instance_id():
		claims.erase(key)
		reserved_visit_marker.set_meta("visit_claims", claims)
	reserved_visit_marker = null
	reserved_visit_slot = -1


func _get_visit_claims(marker: Marker2D) -> Dictionary:
	if marker.has_meta("visit_claims"):
		var raw_claims: Variant = marker.get_meta("visit_claims")
		if raw_claims is Dictionary:
			return raw_claims as Dictionary
	return {}


func _first_free_visit_slot(claims: Dictionary) -> int:
	for index in range(maxi(1, visit_slot_count)):
		if !claims.has(str(index)):
			return index
	return rng.randi_range(0, maxi(0, visit_slot_count - 1))


func _play_idle() -> void:
	animation_player.play(idle_animation)


func _play_seat_animation(marker: Marker2D) -> void:
	# 椅子朝向放在 Marker 的元数据里，动画切换不依赖硬编码坐标。
	var direction := StringName(str(marker.get_meta("seat_direction", "down")))
	match direction:
		&"up":
			animation_player.play(seat_up_animation)
		&"left":
			animation_player.play(seat_left_animation)
		&"right":
			animation_player.play(seat_right_animation)
		_:
			animation_player.play(seat_down_animation)


func get_debug_state() -> Dictionary:
	# 调试面板读取这里，避免 OfficeDemo 直接依赖角色内部变量名。
	var target_name := ""
	var target_group := ""
	if current_target != null:
		target_name = str(current_target.name)
		if current_target.is_in_group(seat_marker_group):
			target_group = str(seat_marker_group)
		elif current_target.is_in_group(idle_marker_group):
			target_group = str(idle_marker_group)
		elif current_target.is_in_group(roam_marker_group):
			target_group = str(roam_marker_group)

	var reserved_name := ""
	if reserved_seat != null:
		reserved_name = str(reserved_seat.name)

	return {
		"position": global_position,
		"target": target_name,
		"target_group": target_group,
		"is_seat": current_target_is_seat,
		"override": has_target_position_override,
		"target_position": _get_target_position(),
		"external_decision": external_decision_enabled,
		"velocity": velocity,
		"stuck_time": stuck_time,
			"reserved": reserved_name,
			"visit_slot": reserved_visit_slot,
			"animation": str(animation_player.current_animation),
		"navigation_finished": navigation_agent.is_navigation_finished(),
		"navigation_target": navigation_agent.target_position,
	}


func _on_wait_timer_timeout() -> void:
	if external_decision_enabled:
		decision_requested.emit()
		return
	_choose_next_target()


func _on_velocity_computed(safe_velocity: Vector2) -> void:
	velocity = safe_velocity
	move_and_slide()
	_update_stuck_state(get_physics_process_delta_time())
