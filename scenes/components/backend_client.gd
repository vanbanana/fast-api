extends Node
## 后端 WebSocket 客户端：负责连接、重连、收发 JSON，不关心业务。

signal connected
signal disconnected
signal command_received(command: Dictionary)
signal parse_failed(text: String)
signal message_sent(summary: String)
signal send_blocked(summary: String)

var url: String = "ws://127.0.0.1:8000/ws/office"
var reconnect_seconds: float = 3.0

var _socket := WebSocketPeer.new()
var _is_open: bool = false
var _reconnect_timer: float = 0.0


func _ready() -> void:
	connect_backend()


func _process(delta: float) -> void:
	var state := _socket.get_ready_state()
	if state != WebSocketPeer.STATE_CLOSED:
		_socket.poll()
		state = _socket.get_ready_state()

	if state == WebSocketPeer.STATE_OPEN:
		if !_is_open:
			_is_open = true
			connected.emit()
		_read_commands()
		return

	if _is_open:
		_is_open = false
		disconnected.emit()

	_reconnect_timer += delta
	if _reconnect_timer >= reconnect_seconds:
		connect_backend()


func connect_backend() -> void:
	# WebSocketPeer 关闭后重新创建，避免复用旧连接状态。
	_socket = WebSocketPeer.new()
	_reconnect_timer = 0.0
	var error := _socket.connect_to_url(url)
	if error != OK:
		_is_open = false
		disconnected.emit()


func is_connected_to_backend() -> bool:
	return _is_open


func send_json(payload: Dictionary) -> void:
	if _socket.get_ready_state() != WebSocketPeer.STATE_OPEN:
		send_blocked.emit("SEND_BLOCKED socket=%s %s" % [socket_state_name(), payload_summary(payload)])
		return
	message_sent.emit(payload_summary(payload))
	_socket.send_text(JSON.stringify(payload))


func socket_state_name() -> String:
	match _socket.get_ready_state():
		WebSocketPeer.STATE_CONNECTING:
			return "CONNECTING"
		WebSocketPeer.STATE_OPEN:
			return "OPEN"
		WebSocketPeer.STATE_CLOSING:
			return "CLOSING"
		WebSocketPeer.STATE_CLOSED:
			return "CLOSED"
		_:
			return str(_socket.get_ready_state())


func payload_summary(payload: Dictionary) -> String:
	var payload_value: Variant = payload.get("payload", {})
	var detail := ""
	if payload_value is Dictionary:
		var payload_dict := payload_value as Dictionary
		if payload_dict.has("text"):
			var text := str(payload_dict.get("text", ""))
			if text.length() > 80:
				text = text.substr(0, 80) + "..."
			detail = " text=%s" % text
		elif payload_dict.has("targets") and payload_dict["targets"] is Array:
			detail = " targets=%s" % str((payload_dict["targets"] as Array).size())
	return "%s worker=%s%s" % [
		str(payload.get("type", "")),
		str(payload.get("worker_id", "")),
		detail,
	]


func _read_commands() -> void:
	while _socket.get_available_packet_count() > 0:
		var text := _socket.get_packet().get_string_from_utf8()
		var data: Variant = JSON.parse_string(text)
		if data is Dictionary:
			command_received.emit(data as Dictionary)
		else:
			parse_failed.emit(text)
