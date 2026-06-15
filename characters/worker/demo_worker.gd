class_name DemoWorker
extends CharacterBody2D

signal target_reached(target: Marker2D)
signal decision_requested()

@export_category("移动参数")
@export var walk_speed: float = 48.0
@export var arrive_distance: float = 3.0
@export var min_wait_time: float = 1.2
@export var max_wait_time: float = 3.0
@export var repath_when_stuck_time: float = 1.2

@export_category("角色资源")
@export var character_texture: Texture2D

@export_category("目标来源")
@export var seat_marker_group: StringName = &"seat_markers"
@export var idle_marker_group: StringName = &"idle_markers"
@export var roam_marker_group: StringName = &"roam_markers"
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
@export var walk_down_animation: StringName = &"walk_down"
@export var walk_up_animation: StringName = &"walk_up"
@export var walk_left_animation: StringName = &"walk_left"
@export var walk_right_animation: StringName = &"walk_right"
@export var idle_down_animation: StringName = &"idle_down"
@export var idle_up_animation: StringName = &"idle_up"
@export var idle_left_animation: StringName = &"idle_left"
@export var idle_right_animation: StringName = &"idle_right"
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
var speech_pause_time: float = 0.0
var _last_move_dir: StringName = &"down"
var _has_settled: bool = false

# 本地状态机（后端断开时使用）
var state_machine: WorkerStateMachine = null
var worker_energy: float = 1.0
var worker_stress: float = 0.18
var current_task_id: String = ""
var has_active_task: bool = false
# 后端返回的氛围层文本
var atmosphere_say: String = ""
var atmosphere_status: String = ""
var atmosphere_mood: String = ""
# 氛围请求用的角色信息（由 office_demo 在 profile_update 时从后端快照写入）
var worker_role: String = ""
var worker_personality: String = ""
# 最近到达的 Marker 名称（用于 atmosphere 上报位置）
var last_marker: String = ""

# ====== 交互系统字段 ======
# 正在寻找的目标 worker 节点引用（seeking 状态时使用）
var _seek_target_worker: Node2D = null
# 寻找目标时的超时计时器（秒），超过 15 秒没找到就放弃
var _seek_timeout: float = 0.0
const SEEK_TIMEOUT_MAX: float = 15.0
# 上次重新寻路的时间戳，找人时每 2 秒更新一次目标位置
var _last_repath_time: float = 0.0
const REPATH_INTERVAL: float = 2.0
# 当前正在交谈的对方节点
var _chat_partner: Node2D = null
var errand_directive_text: String = ""
# 邻近检测：进入交互范围的同事列表
var _nearby_workers: Array[Node2D] = []
# 邻近检测半径（像素）
const PROXIMITY_RADIUS: float = 35.0
# 面对面交谈时的对话距离
const CHAT_DISTANCE: float = 28.0

# 工作进度计时器（WORKING 状态下每 WORK_PROGRESS_INTERVAL 秒推进一次任务进度）
# 每个worker加随机偏移，避免8人同时请求LLM导致限流
var _work_progress_timer: float = 0.0
const WORK_PROGRESS_INTERVAL: float = 5.0
var _work_progress_offset: float = 0.0


func _ready() -> void:
	rng.randomize()
	# 每个人步速略有差异，避免所有角色同速同步的机械感。
	walk_speed *= rng.randf_range(0.82, 1.18)
	# 进度上报随机偏移0~4秒，错开LLM请求
	_work_progress_offset = rng.randf_range(0.0, 4.0)
	navigation_agent.target_desired_distance = arrive_distance
	navigation_agent.path_desired_distance = arrive_distance
	# 角色间避让走软分离（_get_separation_velocity），不用 RVO：
	# RVO 的 velocity_computed 每个物理帧都会回调，会把静止角色推走、移动角色原地打转。
	navigation_agent.avoidance_enabled = false
	wait_timer.timeout.connect(_on_wait_timer_timeout)

	if character_texture:
		sprite.texture = character_texture

	# 初始化本地状态机
	state_machine = WorkerStateMachine.new()
	state_machine.energy = worker_energy
	state_machine.stress = worker_stress

	# 启用邻近检测 Area2D（场景中已有 captureArea 节点但未使用）
	_setup_proximity_area()

	# 起点也由场景里的 Marker 管理，便于在编辑器拖拽调整。
	if start_marker:
		global_position = start_marker.global_position
	else:
		# 自动寻址：如果未配置 start_marker，自动寻找 "{自己名字}Marker" 并瞬移定位出生
		var own_desk := _find_own_desk_marker()
		if own_desk != null:
			global_position = own_desk.global_position
	last_position = global_position
	default_z_index = z_index

	call_deferred("_start_after_navigation_sync")


func _start_after_navigation_sync() -> void:
	# 等待导航地图完成同步，避免开场第一帧拿到空路径后原地播放走路。
	await get_tree().physics_frame
	if external_decision_enabled:
		wait_timer.start(rng.randf_range(0.2, 0.5))
		return
	# 开场先去自己工位就坐，像正常公司上班一样，不乱逛。
	_go_to_own_desk_first()


func _go_to_own_desk_first() -> void:
	var own_desk := _find_own_desk_marker()
	if own_desk == null:
		# 场景里没有对应工位标记，回退到普通随机逻辑。
		_has_settled = true
		_choose_next_target()
		return
	_begin_target(own_desk, true)


func _physics_process(delta: float) -> void:
	# 说话暂停优先于移动判断，即使已到达目标（current_target 为空）也要处理。
	if speech_pause_time > 0.0:
		speech_pause_time -= delta
		velocity = Vector2.ZERO
		move_and_slide()
		if speech_pause_time <= 0.0:
			_play_idle()
			# 交谈结束，退出 CHATTING 状态
			if state_machine != null and state_machine.current_state == WorkerStateMachine.State.CHATTING:
				state_machine.end_chatting()
				_chat_partner = null
		return

	# 非外部决策模式下，通过状态机驱动能量/压力衰减和状态转换
	if !external_decision_enabled and state_machine != null:
		state_machine.has_active_task = has_active_task
		var sm_result := state_machine.tick(delta)
		# 同步状态机中的能量/压力值到角色字段，供外部读取
		worker_energy = state_machine.energy
		worker_stress = state_machine.stress
		# 状态刚切换到 WORKING 时立即更新状态气泡为绿色"工作中"
		if sm_result.state_changed and state_machine.current_state == WorkerStateMachine.State.WORKING:
			var office_demo := get_node_or_null(^"/root/OfficeDemo")
			if office_demo and office_demo.has_method(&"update_worker_status"):
				office_demo.update_worker_status(str(name), "工作中")

	# ====== 工作进度计时器 ======
	if state_machine != null and state_machine.current_state == WorkerStateMachine.State.WORKING:
		_work_progress_timer += delta
		if _work_progress_timer >= WORK_PROGRESS_INTERVAL + _work_progress_offset and current_task_id != "":
			_work_progress_timer = -_work_progress_offset  # 保持错开
			# 通过后端发送 task_progress 事件推进任务进度
			var office_demo := get_node_or_null(^"/root/OfficeDemo")
			if office_demo and office_demo.has_method(&"send_event_to_backend"):
				office_demo.send_event_to_backend({
					"type": "task_progress",
					"worker_id": str(name),
					"payload": {
						"task_id": current_task_id,
						"progress_delta": 0.05,
					},
				})

	# ====== SEEKING 状态：找人逻辑 ======
	if state_machine != null and state_machine.current_state == WorkerStateMachine.State.SEEKING:
		_update_seeking(delta)

	if current_target == null and not has_target_position_override:
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

	_play_walk(direction)

	# 寻路时软分离力度加强，防止多人在狭窄通道卡死
	var sep := _get_separation_velocity()
	if state_machine != null and state_machine.current_state == WorkerStateMachine.State.SEEKING:
		sep *= 1.5  # 找人时更积极地避让

	velocity = direction * walk_speed + sep
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
		wait_timer.start(_natural_wait())
		return

	current_target = _pick_weighted_marker(target_pool)
	current_target_is_seat = next_target_is_seat
	next_target_is_seat = !next_target_is_seat

	if current_target_is_seat:
		_reserve_seat(current_target)

	has_target_position_override = false
	navigation_agent.target_position = _get_target_position()
	stuck_time = 0.0
	last_position = global_position
	_play_walk(global_position.direction_to(_get_target_position()))


func move_to_marker_id(target_id: StringName) -> bool:
	# 给后端 agent 使用的指令入口：后端只传 Marker 名称，具体目标仍从场景分组里查找。
	var marker := _find_marker_by_id(target_id)
	if marker == null:
		return false

	if _is_other_worker_desk(marker):
		return visit_marker_id(target_id)
	if !_is_marker_available(marker):
		return false

	# 已经在目标座位上就坐着不动，避免反复站起来再坐下。
	if reserved_seat == marker and current_target == null:
		wait_timer.start(_natural_wait())
		return true

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
	# 对方不在工位附近时，直奔对方当前位置，而不是去空工位傻等。
	var target_worker := _find_worker_for_desk(marker)
	if target_worker != null and target_worker.global_position.distance_to(marker.global_position) > 40.0:
		target_position_override = target_worker.global_position + Vector2(rng.randf_range(-14.0, 14.0), 14.0)
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
	else:
		# 切换到本地状态机模式，强制重置为 IDLE 状态
		if state_machine != null:
			state_machine.force_idle()


func wait_for_next_decision() -> void:
	# 后端返回 idle/say 时使用，避免员工卡住不再进入自主循环。
	# 已经坐在座位上时保持入座动画，否则会出现"坐着却播放站立动画"的视觉 bug。
	if current_target == null and reserved_seat != null:
		_play_seat_animation(reserved_seat)
	else:
		_play_idle()
	wait_timer.start(_natural_wait())


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
	_play_walk(global_position.direction_to(_get_target_position()))


func _arrive_at_target() -> void:
	velocity = Vector2.ZERO
	move_and_slide()

	if current_target == null:
		_play_idle()
		# 通知状态机已到达非工位位置
		if state_machine != null and state_machine.current_state == WorkerStateMachine.State.SEEKING:
			# 找人状态的下一步移动由 _update_seeking 处理
			pass
		else:
			if state_machine != null:
				state_machine.notify_arrived("idle_point")
			target_reached.emit(null)
			wait_timer.start(_natural_wait())
		has_target_position_override = false
		stuck_time = 0.0
		last_position = global_position
		return

	# 清理临时 seek marker（找人时创建的虚拟目标，向下兼容）
	if current_target.has_meta("_is_temp_seek"):
		current_target.queue_free()
		current_target = null
		has_target_position_override = false
		stuck_time = 0.0
		last_position = global_position
		# 到达追踪位置后不立即 wait——让 seeking 逻辑决定下一步（可能触发交谈）
		return

	if current_target_is_seat:
		global_position = _get_seated_position(current_target)
		_apply_seat_sorting(current_target)
		_play_seat_animation(current_target)
		# 第一次到达自己工位，标记就位完成，之后才进入正常循环。
		if !_has_settled and str(current_target.name) == "%sMarker" % str(name):
			_has_settled = true
	# 通知状态机已到达工位
		if state_machine != null:
			state_machine.notify_arrived("desk")
		last_marker = str(current_target.name)
	else:
		if has_target_position_override:
			global_position = target_position_override
		_play_idle()
		# 通知状态机已到达非工位位置（休息区/漫游点）
		if state_machine != null:
			state_machine.notify_arrived("idle_point")

	target_reached.emit(current_target)
	current_target = null
	has_target_position_override = false
	stuck_time = 0.0
	last_position = global_position
	wait_timer.start(_natural_wait())


func pause_for_speech(char_count: int, face_point: Vector2 = Vector2.INF) -> void:
	# 说话时长随台词长度估算，站住期间面向对方。
	# 不再要求 current_target 非空：到达工位后 target 已清空，但寒暄仍需停下。
	speech_pause_time = clampf(1.2 + float(char_count) / 9.0, 1.6, 6.0)
	if face_point != Vector2.INF:
		sprite.flip_h = face_point.x < global_position.x
	velocity = Vector2.ZERO
	_play_idle()


func _natural_wait() -> float:
	# 偏短停留为主、偶尔长停留，比均匀随机更像真人节奏。
	var base := min_wait_time + (max_wait_time - min_wait_time) * pow(rng.randf(), 2.0)
	if rng.randf() < 0.15:
		base *= 2.5
	return base


func _pick_weighted_marker(pool: Array[Marker2D]) -> Marker2D:
	# 就近优先的加权随机：近处目标权重高，但仍有概率去远处。
	var weights: Array[float] = []
	var total := 0.0
	for marker in pool:
		var w := 1.0 / (global_position.distance_to(marker.global_position) + 60.0)
		weights.append(w)
		total += w
	var roll := rng.randf() * total
	for i in pool.size():
		roll -= weights[i]
		if roll <= 0.0:
			return pool[i]
	return pool[pool.size() - 1]


func _find_worker_for_desk(marker: Marker2D) -> Node2D:
	var marker_name := str(marker.name)
	if !marker_name.ends_with("Marker"):
		return null
	var owner_name := marker_name.trim_suffix("Marker")
	for node in get_tree().get_nodes_in_group(worker_group):
		if node is Node2D and str(node.name) == owner_name:
			return node
	return null


func _find_own_desk_marker() -> Marker2D:
	# 找到名称为 "{自己名字}Marker" 的座位标记。
	var expected_name := StringName("%sMarker" % str(name))
	for node in get_tree().get_nodes_in_group(seat_marker_group):
		if node is Marker2D and node.name == expected_name:
			return node as Marker2D
	return null


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
	var displacement := global_position.distance_to(last_position)
	if displacement < 0.25:
		stuck_time += delta
	else:
		stuck_time = 0.0
		last_position = global_position

	if stuck_time < repath_when_stuck_time:
		return

	# 防卡死策略：根据当前状态选择不同恢复方式
	if state_machine != null and state_machine.current_state == WorkerStateMachine.State.SEEKING:
		# 找人时卡住 → 重新寻路到目标当前位置（目标可能移动了）
		if _seek_target_worker != null:
			_do_seek_pathfind()
			stuck_time = 0.0
			return

	# 默认：释放当前目标，重新选一个
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


func _get_direction_name(dir: Vector2) -> StringName:
	if absf(dir.x) >= absf(dir.y):
		return &"left" if dir.x < 0 else &"right"
	return &"up" if dir.y < 0 else &"down"


func _play_walk(dir: Vector2) -> void:
	_last_move_dir = _get_direction_name(dir)
	sprite.flip_h = false
	match _last_move_dir:
		&"up":
			animation_player.play(walk_up_animation)
		&"left":
			animation_player.play(walk_left_animation)
		&"right":
			animation_player.play(walk_right_animation)
		_:
			animation_player.play(walk_down_animation)


func _play_idle() -> void:
	sprite.flip_h = false
	match _last_move_dir:
		&"up":
			animation_player.play(idle_up_animation)
		&"left":
			animation_player.play(idle_left_animation)
		&"right":
			animation_player.play(idle_right_animation)
		_:
			animation_player.play(idle_down_animation)


func _play_seat_animation(marker: Marker2D) -> void:
	# 椅子朝向放在 Marker 的元数据里，动画切换不依赖硬编码坐标。
	sprite.flip_h = false
	var direction := StringName(str(marker.get_meta("seat_direction", "down")))

	_last_move_dir = direction
	match direction:
		&"up":
			animation_player.play(seat_up_animation)
		&"left":
			animation_player.play(seat_left_animation)
		&"right":
			animation_player.play(seat_right_animation)
		_:
			animation_player.play(seat_down_animation)


## ====== 邻近检测（使用场景中已有的 captureArea Area2D）======

## 初始化邻近检测区域。启用 captureArea 的碰撞检测，监听 body_entered/body_exited。
func _setup_proximity_area() -> void:
	var area := get_node_or_null(^"captureArea")
	if area == null or !(area is Area2D):
		push_warning("%s: captureArea 节点不存在或不是 Area2D，邻近检测不可用" % name)
		return
	var shape := area.get_node_or_null(^"CollisionShape2D")
	if shape == null or !(shape is CollisionShape2D):
		push_warning("%s: captureArea 下没有 CollisionShape2D" % name)
		return
	(shape as CollisionShape2D).disabled = false
	# 设置圆形检测形状（只创建一次，后续复用）
	var existing_shape: Shape2D = shape.shape
	if existing_shape == null or !(existing_shape is CircleShape2D):
		var circle := CircleShape2D.new()
		circle.radius = PROXIMITY_RADIUS
		shape.shape = circle
	elif existing_shape is CircleShape2D:
		(existing_shape as CircleShape2D).radius = PROXIMITY_RADIUS
	area.monitoring = true
	area.monitorable = true
	area.body_entered.connect(_on_proximity_body_entered)
	area.body_exited.connect(_on_proximity_body_exited)


func _on_proximity_body_entered(body: Node) -> void:
	if !(body is Node2D) or body == self or !body.is_in_group(worker_group):
		return
	if _nearby_workers.has(body):
		return
	_nearby_workers.append(body)


func _on_proximity_body_exited(body: Node) -> void:
	_nearby_workers.erase(body)


## 获取当前附近的同事列表（供 office_demo 寒暄触发使用）。
func get_nearby_workers() -> Array[Node2D]:
	return _nearby_workers.duplicate()


## ====== 智能找人系统 ======

## 公开接口：让这个员工去找另一个员工。
## 智能搜索策略：
##   1. 目标在工位上 → 走到目标工位旁（visit 定位）
##   2. 目标不在工位 → 直接追踪目标的当前位置（每 2 秒重新寻路）
##   3. 超过 15 秒没找到 → 放弃，回到自己的活动
##   4. 找到后自动进入面对面交谈状态
func seek_worker(target_worker: Node2D) -> bool:
	if target_worker == null or state_machine == null:
		return false
	# 防止找自己
	if target_worker == self:
		return false
	# 如果已经在跟这个人说话，不再重复开始
	if _chat_partner == target_worker:
		return false
	# 并发保护：已在对话中则拒绝
	if _chat_partner != null:
		return false
	_seek_target_worker = target_worker
	_seek_timeout = 0.0
	_last_repath_time = 0.0
	state_machine.begin_seeking()
	_do_seek_pathfind()
	return true


## 强制回到空闲状态（由 A2A 控制器在对话结束/超时时调用）。
func force_idle() -> void:
	if state_machine != null:
		state_machine.force_idle()
	_chat_partner = null
	_seek_target_worker = null
	_seek_timeout = 0.0
	errand_directive_text = ""
	current_target = null
	has_target_position_override = false
	velocity = Vector2.ZERO
	_play_idle()
	wait_timer.start(_natural_wait())


## 回到自己的工位（force_idle 后立刻导航）。
func return_to_seat() -> void:
	force_idle()
	var own_desk := _find_own_desk_marker()
	if own_desk != null:
		_begin_target(own_desk, true)
	else:
		_choose_next_target()


## 取消当前找人行为（外部强制中断时使用）。
func cancel_seeking() -> void:
	if state_machine == null:
		return
	# 同时处理 SEEKING 和 CHATTING（找人找到后进入交谈，取消时一并清理）
	if state_machine.current_state == WorkerStateMachine.State.SEEKING:
		state_machine.end_seeking()
	elif state_machine.current_state == WorkerStateMachine.State.CHATTING:
		state_machine.end_chatting()
	_chat_partner = null
	_seek_target_worker = null
	_seek_timeout = 0.0
	current_target = null
	has_target_position_override = false
	_play_idle()
	wait_timer.start(_natural_wait())
	errand_directive_text = ""


## 会议中断：保存当前工作状态，清空 active_task_id，设置 status。
func interrupt_for_meeting(directive_text: String) -> void:
	if state_machine == null:
		return
	# 保存当前活跃任务信息到中断字段（供 finish_meeting 恢复）
	if current_task_id != "":
		atmosphere_status = "被叫去开会，暂停当前任务"
	else:
		atmosphere_status = "去会议室讨论"
	atmosphere_say = "好的，我马上过去。"
	current_task_id = ""
	has_active_task = false
	if state_machine.current_state == WorkerStateMachine.State.WORKING:
		state_machine.force_idle()


## 会议结束：恢复之前被中断的任务状态。
func finish_meeting() -> void:
	if state_machine == null:
		return
	atmosphere_status = "会议结束，回到原任务"
	atmosphere_say = "会议结束了，回去继续干活。"
	has_active_task = true
	if state_machine.current_state == WorkerStateMachine.State.IDLE:
		# 状态机会在下一 tick 自动从 IDLE → WORKING（因为 has_active_task=true 且 at_own_desk）
		pass


## SEEKING 状态逐帧更新：超时检查 + 动态重寻路 + 到达检测。
func _update_seeking(delta: float) -> void:
	_seek_timeout += delta
	if _seek_timeout > SEEK_TIMEOUT_MAX:
		cancel_seeking()
		atmosphere_status = "没找到人，回工位"
		atmosphere_say = "算了，先回去干活。"
		return

	_last_repath_time += delta
	if _last_repath_time >= REPATH_INTERVAL:
		_do_seek_pathfind()
		_last_repath_time = 0.0

	# 接近检测：距离目标 < CHAT_DISTANCE+10 时触发交谈
	if _seek_target_worker != null:
		var dist := global_position.distance_to(_seek_target_worker.global_position)
		if dist < CHAT_DISTANCE + 10.0:
			_start_face_to_face_chat(_seek_target_worker)


## 根据目标当前位置决定走哪里。
func _do_seek_pathfind() -> void:
	if _seek_target_worker == null:
		return
	var target_pos := _seek_target_worker.global_position
	var desk_marker := _find_own_desk_marker_for(_seek_target_worker)

	# 目标是否在工位附近？
	var at_desk := false
	if desk_marker != null:
		at_desk = _seek_target_worker.global_position.distance_to(desk_marker.global_position) < 25.0

	if at_desk:
		_visit_worker_at_desk(_seek_target_worker, desk_marker)
	else:
		_navigate_to_position(target_pos + _offset_around(target_pos))


## 走到目标员工的工位旁边。
func _visit_worker_at_desk(target_worker: Node2D, desk_marker: Marker2D) -> void:
	wait_timer.stop()
	_reserve_visit_slot(desk_marker)
	target_position_override = desk_marker.global_position + _get_visit_offset(desk_marker, reserved_visit_slot)
	has_target_position_override = true
	_begin_target(desk_marker, false)


## 导航到一个绝对位置（用于追踪移动中的目标）。
func _navigate_to_position(pos: Vector2) -> void:
	wait_timer.stop()
	target_position_override = pos
	has_target_position_override = true

	z_index = default_z_index
	_release_reserved_seat()
	_release_reserved_visit_slot()
	current_target = null
	current_target_is_seat = false
	next_target_is_seat = true

	navigation_agent.target_position = pos
	stuck_time = 0.0
	last_position = global_position
	_play_walk(global_position.direction_to(pos))


## 清理所有临时的 seek marker（repath 和到达时都调用）。
func _cleanup_temp_seek_markers() -> void:
	if get_parent() == null:
		return
	for child in get_parent().get_children():
		if child is Marker2D and child.has_meta("_is_temp_seek"):
			child.queue_free()


func _offset_around(target_pos: Vector2) -> Vector2:
	return Vector2(rng.randf_range(-18.0, 18.0), rng.randf_range(-10.0, 10.0))


## 通过对方名字找其工位 marker。
func _find_own_desk_marker_for(worker: Node2D) -> Marker2D:
	if worker == null:
		return null
	var expected_name := StringName("%sMarker" % str(worker.name))
	for node in get_tree().get_nodes_in_group(seat_marker_group):
		if node is Marker2D and node.name == expected_name:
			return node as Marker2D
	return null


## ====== 面对面交谈 ======

## 开始与目标员工面对面交谈。流程：停下→面向对方→双方暂停→气泡→结束恢复。
signal chat_started(speaker: Node2D, listener: Node2D)
signal seek_finished()

func _start_face_to_face_chat(partner: Node2D) -> void:
	if partner == null:
		return

	# 停止一切移动
	velocity = Vector2.ZERO
	move_and_slide()
	navigation_agent.target_position = global_position
	current_target = null
	has_target_position_override = false
	_release_reserved_seat()
	_release_reserved_visit_slot()

	# 清理临时 seek marker
	_cleanup_temp_seek_markers()

	# 进入 CHATTING 状态
	_chat_partner = partner
	if state_machine != null:
		state_machine.begin_chatting()

	# 双方互相面向
	face_toward(partner.global_position)
	if partner.has_method(&"face_toward"):
		partner.face_toward(global_position)

	# 双方暂停说话时长
	var chat_text := "%s过来聊两句。" % str(name)
	pause_for_speech(chat_text.length(), partner.global_position)
	if partner.has_method(&"pause_for_speech"):
		partner.pause_for_speech(chat_text.length(), global_position)

	chat_started.emit(self, partner)
	_seek_target_worker = null
	seek_finished.emit()


## 面向指定位置。
func face_toward(position: Vector2) -> void:
	sprite.flip_h = position.x < global_position.x


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
		# 状态机调试信息
		"sm_state": state_machine.get_state_name() if state_machine != null else "NONE",
		"sm_energy": worker_energy,
		"sm_stress": worker_stress,
		# 交互系统调试信息（安全访问，防止节点已释放）
		"nearby_count": _nearby_workers.size(),
		"seeking": is_instance_valid(_seek_target_worker),
		"seek_target": str(_seek_target_worker.name) if is_instance_valid(_seek_target_worker) else "",
		"seek_timeout": _seek_timeout,
		"chatting": is_instance_valid(_chat_partner),
		"chat_partner": str(_chat_partner.name) if is_instance_valid(_chat_partner) else "",
		"errand_directive_text": errand_directive_text,
	}


func _on_wait_timer_timeout() -> void:
	if external_decision_enabled:
		decision_requested.emit()
		return
	# SEEKING / CHATTING 状态由交互系统控制，不进入本地循环
	if state_machine != null and state_machine.current_state in [WorkerStateMachine.State.SEEKING, WorkerStateMachine.State.CHATTING]:
		# 寻人中：保持当前导航不中断；交谈中：保持暂停
		if current_target == null and state_machine.current_state == WorkerStateMachine.State.SEEKING:
			# seek 路径已走完但还没触发交谈 → 重新寻路
			_do_seek_pathfind()
		return
	# 本地决策模式：根据状态机的建议选择目标
	if state_machine != null:
		var sm_result := state_machine.tick(0.0)
		match sm_result.target_type:
			"own_desk":
				var own_desk := _find_own_desk_marker()
				if own_desk != null:
					_begin_target(own_desk, true)
				else:
					_choose_next_target()
			"idle_point":
				var idle_pool := _collect_available_markers(idle_marker_group)
				if !idle_pool.is_empty():
					var target := _pick_weighted_marker(idle_pool)
					_begin_target(target, false)
				else:
					_play_idle()
					wait_timer.start(_natural_wait())
			_:
				# "stay" 或其他情况，保持原地
				_play_idle()
				wait_timer.start(_natural_wait())
	else:
		_choose_next_target()
