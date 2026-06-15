extends RefCounted
## 后端 agent 快照/工作上下文/思考流缓存，供详情面板和 debug 面板读取。

var snapshots: Dictionary = {}
var work_contexts: Dictionary = {}
var streams: Dictionary = {}


func cache_command(command: Dictionary) -> void:
	var worker_id := str(command.get("worker_id", ""))
	if worker_id.is_empty() or worker_id == "office":
		return

	var say := str(command.get("say", ""))
	if str(command.get("action", "")) == "stream_delta":
		if say.is_empty():
			return
		var stream_value: Variant = streams.get(worker_id, [])
		var stream: Array = []
		if stream_value is Array:
			stream = stream_value as Array
		stream.append(say)
		if stream.size() > 24:
			stream = stream.slice(stream.size() - 24)
		streams[worker_id] = stream
		return

	var payload: Variant = command.get("payload", {})
	if payload is Dictionary and !payload.is_empty():
		# 合并而非替换，避免 chat_line 等命令冲掉 profile 中的 name/role
		var existing_value: Variant = snapshots.get(worker_id, {})
		var existing: Dictionary = {}
		if existing_value is Dictionary:
			existing = existing_value as Dictionary
		var p := payload as Dictionary
		existing.merge(p, true)   # true = overwrite existing keys
		snapshots[worker_id] = existing
		if p.has("work_context") and p["work_context"] is Dictionary:
			work_contexts[worker_id] = p["work_context"]

	if !say.is_empty():
		if !snapshots.has(worker_id):
			snapshots[worker_id] = {}
		var snapshot: Dictionary = snapshots[worker_id] as Dictionary
		snapshot["last_say"] = say
		snapshots[worker_id] = snapshot


func clear() -> void:
	snapshots.clear()
	work_contexts.clear()
	streams.clear()


func snapshot_for(worker_id: String) -> Dictionary:
	var value: Variant = snapshots.get(worker_id, {})
	if value is Dictionary:
		return value as Dictionary
	return {}


func context_for(worker_id: String) -> Dictionary:
	var value: Variant = work_contexts.get(worker_id, {})
	if value is Dictionary:
		return value as Dictionary
	return {}


func stream_for(worker_id: String) -> Array:
	var value: Variant = streams.get(worker_id, [])
	if value is Array:
		return value as Array
	return []
