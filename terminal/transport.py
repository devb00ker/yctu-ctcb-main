"""每次连接使用全新 Paho client，断线清空 QoS 在途消息，避免旧指令重放。"""
import threading
import time
import paho.mqtt.client as mqtt
from .protocol import UP, DOWN, encode, decode, interval


class Transport:
    def __init__(self, config, state):
        self.config, self.state = config, state
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.client = None
        self.thread = None
        self.ready = False
        self.last_send = {}
        self.pending = {}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="mqtt", daemon=True)
        self.thread.start()

    def _run(self):
        backoff = 1
        while not self.stop_event.is_set():
            dropped = threading.Event()
            subscribed = threading.Event()
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=str(self.config.robot_id),
                                 clean_session=True, protocol=mqtt.MQTTv311, reconnect_on_failure=False)
            client.max_inflight_messages_set(8)
            client.max_queued_messages_set(16)
            client.connect_timeout = 2

            def connected(c, userdata, flags, reason, properties):
                if reason.is_failure:
                    self.state.status(f"连接拒绝：{reason}")
                    dropped.set()
                    return
                rc, _ = c.subscribe([(n, 1) for n in DOWN])
                if rc != mqtt.MQTT_ERR_SUCCESS:
                    dropped.set()
                self.state.status("已连接，等待订阅确认")

            def on_subscribe(c, userdata, mid, reasons, properties):
                if len(reasons) != len(DOWN) or any(r.is_failure for r in reasons):
                    self.state.status("订阅被拒绝，稍后重试")
                    dropped.set()
                    return
                with self.lock:
                    self.ready = True
                subscribed.set()
                self.state.status(f"已就绪 · {self.config.host}:{self.config.port} · ID {self.config.robot_id}", True)

            def disconnected(c, userdata, flags, reason, properties):
                with self.lock:
                    self.ready = False
                self.state.status(f"连接断开：{reason}")
                dropped.set()

            def message(c, userdata, packet):
                if packet.topic not in DOWN:
                    return
                try:
                    data = decode(packet.topic, packet.payload, self.config.radar_format)
                    self.state.message("rx", packet.topic, data, packet.payload)
                except Exception as exc:
                    with self.state.lock:
                        self.state.rejected += 1
                    self.state.log(f"丢弃 {packet.topic}：{exc}")

            client.on_connect, client.on_subscribe = connected, on_subscribe
            client.on_disconnect, client.on_message = disconnected, message
            with self.lock:
                self.client, self.ready = client, False
                self.pending.clear()
                self.last_send.clear()
            started = False
            try:
                self.state.status(f"连接中 · {self.config.host}:{self.config.port}")
                client.connect(self.config.host, self.config.port, keepalive=5)
                client.loop_start()
                started = True
                deadline = time.monotonic() + 5
                while not self.stop_event.wait(0.05) and not dropped.is_set():
                    with self.lock:
                        self.pending = {mid: item for mid, item in self.pending.items() if not item[1].is_published()}
                        ack_timeout = any(time.monotonic()-item[0] > 2 for item in self.pending.values())
                    if ack_timeout:
                        self.state.status("指令确认超时，重建连接并清除待发指令")
                        break
                    if subscribed.is_set():
                        backoff = 1
                    elif time.monotonic() > deadline:
                        self.state.status("连接或订阅超时")
                        break
            except OSError as exc:
                self.state.status(f"连接失败：{exc}")
            finally:
                with self.lock:
                    self.ready = False
                    self.client = None
                    self.pending.clear()
                client.disconnect()
                if started:
                    client.loop_stop()
            if not self.stop_event.is_set():
                self.state.status(f"{backoff}s 后重连；控制已释放")
                self.stop_event.wait(backoff)
                backoff = min(5, backoff * 2)
        self.state.status("已断开")

    def send(self, topic, data):
        if topic not in UP:
            raise ValueError("终端只能发送 9 条上行指令")
        payload = encode(topic, data)
        now = time.monotonic()
        with self.lock:
            if not self.ready or not self.client or not self.client.is_connected():
                raise ConnectionError("未连接或订阅未完成；指令未发送")
            if now - self.last_send.get(topic, -1e10) < interval(topic, self.config.map_role):
                raise ValueError(f"{topic} 发送过快")
            if len(self.pending) >= 8:
                raise BufferError("等待 broker 确认的指令过多")
            qos = 0 if topic in ("KeyboardMouseControl", "CustomControl") else 1
            info = self.client.publish(topic, payload, qos=qos, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise ConnectionError(f"发送失败：{mqtt.error_string(info.rc)}")
            if qos:
                # 使用 MessageInfo 查询完成状态，避免在 Paho 内部锁回调中反向取得应用锁。
                self.pending[info.mid] = (now, info)
            self.last_send[topic] = now
        self.state.message("tx", topic, decode(topic, payload), payload)
        return info

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.ready = False
            if self.client:
                self.client.disconnect()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self.state.log("MQTT 线程尚在退出，等待系统网络调用返回")
