class_name WorkerStateMachine
extends RefCounted

## 员工本地有限状态机：管理 IDLE / WORKING / BREAK / ROAMING 四种状态的自动转换。
## SEEKING 和 CHATTING 是外部覆盖状态（找人/交谈），由 demo_worker 显式控制，
## 进入后暂停自动转换，直到外部调用 end_seeking() / end_chatting() 才恢复。
## 后端断开时，DemoWorker 通过 tick() 驱动本地行为循环。

enum State { IDLE, WORKING, BREAK, ROAMING, SEEKING, CHATTING }

# 当前状态
var current_state: State = State.IDLE
# 能量值 [0.0, 1.0]，工作时衰减，休息时恢复
var energy: float = 1.0
# 压力值 [0.0, 1.0]，工作时增长，休息时降低
var stress: float = 0.18
# 是否有活跃任务（由外部设置）
var has_active_task: bool = false
# 是否在自己的工位上（到达工位后设为 true，离开时设为 false）
var at_own_desk: bool = false
# 全局离座计数：所有状态机共享，限制同时离座人数 ≤ 3
static var shared_away_count: int = 0
const MAX_AWAY_COUNT: int = 3
# 休息计时器（秒），进入 BREAK 时随机初始化为 15~30 秒
var break_timer: float = 0.0
# 随机数生成器
var rng := RandomNumberGenerator.new()

## 状态转换结果字典：包含建议的目标类型和是否发生状态切换。
## target_type 可选 "own_desk" | "idle_point" | "stay"
class TickResult:
	var target_type: String = "stay"
	var state_changed: bool = false


func _init() -> void:
	rng.randomize()


## 每帧调用，根据当前状态和时间步长更新能量/压力并判断是否需要转换状态。
## SEEKING / CHATTING 状态下不执行自动转换，由外部控制退出。
## 返回 TickResult，指示下一步应该去哪里。
func tick(delta: float) -> TickResult:
	# 能量/压力自动衰减与恢复（所有状态都继续衰减）
	_match_energy_stress_decay(delta)

	# 休息计时器倒计时
	if current_state == State.BREAK and break_timer > 0.0:
		break_timer -= delta

	var result := TickResult.new()
	# SEEKING / CHATTING 是外部控制状态，不自动转换
	if current_state in [State.SEEKING, State.CHATTING]:
		result.target_type = "stay"
		return result
	result.state_changed = _try_state_transition(result)
	return result


## 根据当前状态更新能量和压力的衰减/恢复。
func _match_energy_stress_decay(delta: float) -> void:
	match current_state:
		State.WORKING:
			energy -= delta * 0.008
			stress += delta * 0.003
		State.BREAK:
			energy += delta * 0.04
			stress -= delta * 0.02
		State.ROAMING:
			energy -= delta * 0.004
			stress += delta * 0.001
		State.SEEKING:
			# 找人时走路消耗略高于漫游
			energy -= delta * 0.006
			stress += delta * 0.002
		State.CHATTING:
			# 交谈时基本不消耗，轻微恢复（像休息）
			energy += delta * 0.01
			stress -= delta * 0.005
		_:
			pass  # IDLE 不变化

	# 钳位到合法范围
	energy = clampf(energy, 0.0, 1.0)
	stress = clampf(stress, 0.0, 1.0)


## 尝试进行状态转换。返回是否发生了切换。
func _try_state_transition(result: TickResult) -> bool:
	match current_state:
		State.IDLE:
			return _check_idle_to_working(result)

		State.WORKING:
			return _check_working_transitions(result)

		State.BREAK:
			return _check_break_to_working(result)

		State.ROAMING:
			return _check_roaming_to_working(result)

	return false


## IDLE → WORKING: 有任务 && 在自己的工位上
func _check_idle_to_working(result: TickResult) -> bool:
	if has_active_task and at_own_desk:
		current_state = State.WORKING
		result.target_type = "stay"
		return true
	return false


## WORKING → BREAK 或 WORKING → ROAMING
func _check_working_transitions(result: TickResult) -> bool:
	# WORKING → BREAK: energy < 0.35 且全局离座人数 < 3 且随机概率 < 0.15
	if energy < 0.35 and shared_away_count < MAX_AWAY_COUNT and rng.randf() < 0.15:
		current_state = State.BREAK
		break_timer = rng.randf_range(15.0, 30.0)
		at_own_desk = false
		shared_away_count += 1
		result.target_type = "idle_point"
		return true

	# WORKING → ROAMING: 无任务 且 全局离座人数 < 3 且 随机概率 < 0.08
	if !has_active_task and shared_away_count < MAX_AWAY_COUNT and rng.randf() < 0.08:
		current_state = State.ROAMING
		at_own_desk = false
		shared_away_count += 1
		result.target_type = "idle_point"
		return true

	return false


## BREAK → WORKING: 能量恢复到 > 0.85 或 定时器到期
func _check_break_to_working(result: TickResult) -> bool:
	if energy > 0.85 or break_timer <= 0.0:
		current_state = State.WORKING
		shared_away_count = maxi(0, shared_away_count - 1)
		result.target_type = "own_desk"
		return true
	return false


## ROAMING → WORKING: 到达工位 或 有新任务
func _check_roaming_to_working(result: TickResult) -> bool:
	if at_own_desk or has_active_task:
		current_state = State.WORKING
		shared_away_count = maxi(0, shared_away_count - 1)
		result.target_type = "own_desk"
		return true
	return false


## 外部强制切回 IDLE 状态（断开后端时使用）。
func force_idle() -> void:
	if current_state != State.IDLE:
		if current_state in [State.BREAK, State.ROAMING, State.SEEKING]:
			shared_away_count = maxi(0, shared_away_count - 1)
		current_state = State.IDLE
		at_own_desk = false


## 进入 SEEKING（找人）状态。记录之前的状态以便恢复。
var _state_before_seeking: State = State.IDLE

func begin_seeking() -> void:
	if current_state == State.SEEKING:
		return
	_state_before_seeking = current_state
	# 如果之前不在离座状态，增加离座计数
	if current_state not in [State.BREAK, State.ROAMING, State.SEEKING, State.CHATTING]:
		shared_away_count += 1
	current_state = State.SEEKING
	at_own_desk = false


## 结束 SEEKING 状态，恢复到之前的状态。
func end_seeking() -> void:
	if current_state != State.SEEKING:
		return
	# 恢复离座计数
	shared_away_count = maxi(0, shared_away_count - 1)
	current_state = _state_before_seeking
	# 如果恢复到需要离座的状态，重新计数
	if current_state in [State.BREAK, State.ROAMING]:
		pass  # 已经算过离座了
	at_own_desk = (current_state == State.WORKING)


## 进入 CHATTING（交谈）状态。
var _state_before_chatting: State = State.IDLE

func begin_chatting() -> void:
	if current_state == State.CHATTING:
		return
	_state_before_chatting = current_state
	current_state = State.CHATTING


## 结束 CHATTING 状态，恢复到之前的状态。
func end_chatting() -> void:
	if current_state != State.CHATTING:
		return
	current_state = _state_before_chatting
	at_own_desk = (current_state == State.WORKING)


## 通知状态机角色已到达某个位置类型。
## location_type: "desk" 表示到达工位，其他表示到达休息区/漫游点。
func notify_arrived(location_type: String) -> void:
	match location_type:
		"desk":
			at_own_desk = true
		_:
			at_own_desk = false
			# 如果漫游中到达了休息点，可以保持 ROAMING/BREAK 状态不变


## 获取当前状态的名称字符串（用于调试和 atmosphere 上报）。
func get_state_name() -> String:
	match current_state:
		State.IDLE:     return "IDLE"
		State.WORKING:  return "WORKING"
		State.BREAK:    return "BREAK"
		State.ROAMING:  return "ROAMING"
		State.SEEKING:  return "SEEKING"
		State.CHATTING: return "CHATTING"
	return "UNKNOWN"


## 是否处于可以被外部事件（会议/找人）中断的状态
func can_interrupt_work() -> bool:
	return current_state == State.WORKING
