import json
import threading
import time
from collections import deque
from pathlib import Path


class State:
    """网络线程只更新快照，GUI 定时读取，避免事件队列堆积。"""
    def __init__(self):
        self.lock = threading.RLock()
        self.latest = {}
        self.events = deque(maxlen=200)
        self.link = "未连接"
        self.ready = False
        self.received = 0
        self.sent = 0
        self.rejected = 0
        self.generation = 0
        self.recorder = None

    def status(self, text, ready=False):
        with self.lock:
            self.link, self.ready = text, ready
            if not ready:
                self.generation += 1
            self.events.append((time.time(), text))

    def log(self, text):
        with self.lock:
            self.events.append((time.time(), text))

    def message(self, direction, topic, data, payload, replay=False):
        now = time.time()
        row = dict(time=now, direction=direction, topic=topic, data=data, hex=payload.hex())
        with self.lock:
            if direction == "rx":
                self.received += 1
                self.latest[topic] = dict(data=data, time=now, monotonic=time.monotonic(), hex=payload.hex())
            else:
                self.sent += 1
            if topic in ("Event", "PenaltyInfo", "SentryCtrlResult") or direction == "tx" and topic != "KeyboardMouseControl":
                self.events.append((now, f"{direction} {topic}: {data}"))
            if self.recorder and not replay:
                self.recorder.write(json.dumps(row, ensure_ascii=False) + "\n")
                self.recorder.flush()

    def record(self, path=None):
        with self.lock:
            if self.recorder:
                self.recorder.close()
                self.recorder = None
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                self.recorder = open(path, "w", encoding="utf-8", buffering=1)

    def snapshot(self):
        with self.lock:
            return dict(latest=dict(self.latest), events=list(self.events), link=self.link,
                        ready=self.ready, received=self.received, sent=self.sent,
                        rejected=self.rejected, generation=self.generation)


class Replay:
    def __init__(self, path):
        self.stream = open(path, encoding="utf-8")
        self.first_time = None
        self.start = time.monotonic()
        self.next_row = self._read()

    def _read(self):
        for line in self.stream:
            if len(line) > 262144:
                raise ValueError("回放行过大")
            row = json.loads(line)
            if row["direction"] == "rx":
                float(row["time"])
                bytes.fromhex(row["hex"])
                if self.first_time is None:
                    self.first_time = float(row["time"])
                return row
        return None

    def tick(self, state, radar_format):
        from .protocol import decode, DOWN
        # 限制每次读取量，保持界面可响应。
        for _ in range(200):
            row = self.next_row
            if row is None or float(row["time"]) - self.first_time > time.monotonic() - self.start:
                break
            if row["topic"] in DOWN:
                payload = bytes.fromhex(row["hex"])
                state.message("rx", row["topic"], decode(row["topic"], payload, radar_format), payload, replay=True)
            self.next_row = self._read()
        return self.next_row is not None

    def close(self):
        self.stream.close()
