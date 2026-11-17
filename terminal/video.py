import queue
import socket
import struct
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
import av

HEADER = struct.Struct("!HHI")


def fragment(payload, frame_id, size=1392):
    if not 1 <= size <= 1392 or not payload or len(payload) > 4 * 1024 * 1024:
        raise ValueError("非法帧长度或分片大小")
    if (len(payload) + size - 1) // size > 65536:
        raise ValueError("分片数量超过 uint16")
    return [HEADER.pack(frame_id & 65535, i, len(payload)) + payload[start:start+size]
            for i, start in enumerate(range(0, len(payload), size))]


@dataclass
class Assembly:
    total: int
    created: float
    parts: dict = field(default_factory=dict)
    received: int = 0
    complete: bytes | None = None


class Reassembler:
    def __init__(self, timeout=0.12, max_frames=8):
        self.timeout, self.max_frames = timeout, max_frames
        self.frames = {}
        self.stats = Counter()
        self.last_id = None
        self.last_packet_time = None

    def push(self, packet, now=None):
        now = time.monotonic() if now is None else now
        self.stats["packets"] += 1
        if not 8 < len(packet) <= 1400:
            self.stats["invalid"] += 1
            return []
        fid, index, total = HEADER.unpack_from(packet)
        payload = packet[8:]
        if not 0 < total <= 4 * 1024 * 1024 or index >= min(total, 16384) or len(payload) > total:
            self.stats["invalid"] += 1
            return []
        if self.last_packet_time is not None and now - self.last_packet_time > 2:
            self.frames.clear()
            self.last_id = None
            self.stats["restarts"] += 1
        self.last_packet_time = now
        if self.last_id is not None and not 0 < (fid - self.last_id) % 65536 < 32768:
            self.stats["late"] += 1
            return self.drain(now)
        ready = self.drain(now)
        if fid not in self.frames:
            if len(self.frames) >= self.max_frames:
                oldest = min(self.frames, key=lambda k: self.frames[k].created)
                del self.frames[oldest]
                self.stats["expired"] += 1
            self.frames[fid] = Assembly(total, now)
        frame = self.frames[fid]
        if total != frame.total:
            self.stats["invalid"] += 1
            return ready
        if index in frame.parts:
            self.stats["duplicate"] += 1
            if frame.parts[index] != payload:
                del self.frames[fid]
                self.stats["invalid"] += 1
            return ready
        if index != len(frame.parts):
            self.stats["reordered"] += 1
        frame.parts[index] = payload
        frame.received += len(payload)
        if frame.received > total:
            del self.frames[fid]
            self.stats["invalid"] += 1
        elif frame.received == total and max(frame.parts) + 1 == len(frame.parts):
            frame.complete = b"".join(frame.parts[i] for i in range(len(frame.parts)))
        return ready + self.drain(now)

    def drain(self, now=None):
        now = time.monotonic() if now is None else now
        output = []
        for fid in list(self.frames):
            if not self.frames[fid].complete and now - self.frames[fid].created > self.timeout:
                del self.frames[fid]
                self.stats["expired"] += 1
        while self.frames:
            if self.last_id is None:
                fid = min(self.frames, key=lambda k: self.frames[k].created)
            else:
                fid = min(self.frames, key=lambda k: (k - self.last_id) % 65536)
            f = self.frames[fid]
            if not f.complete:
                break
            distance = 1 if self.last_id is None else (fid - self.last_id) % 65536
            if distance != 1 and now - f.created < self.timeout:
                break
            if distance > 1:
                self.stats["missing"] += distance - 1
            self.last_id = fid
            output.append(f.complete)
            del self.frames[fid]
            self.stats["frames"] += 1
        return output


class VideoReceiver:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.queue = queue.Queue(maxsize=8)
        self.assembler = Reassembler()
        self.stats = Counter()
        self.latest = None
        self.last_frame_time = 0
        self.error = "等待图传"
        self.sock = None
        self.threads = []
        self.reset_decoder = threading.Event()

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        try:
            self.sock.bind((self.config.video_bind, self.config.video_port))
        except OSError:
            self.sock.close()
            self.sock = None
            raise
        self.sock.settimeout(0.03)
        self.stop_event.clear()
        self.threads = [threading.Thread(target=self._receive, name="udp", daemon=True),
                        threading.Thread(target=self._decode, name="hevc", daemon=True)]
        for thread in self.threads:
            thread.start()

    def _receive(self):
        while not self.stop_event.is_set():
            try:
                packet, address = self.sock.recvfrom(65536)
                if self.config.video_source and address[0] != self.config.video_source:
                    with self.lock:
                        self.stats["source_rejected"] += 1
                    continue
                blocks = self.assembler.push(packet)
            except socket.timeout:
                blocks = self.assembler.drain()
            except OSError as exc:
                if not self.stop_event.is_set():
                    with self.lock:
                        self.error = f"UDP 异常：{exc}"
                break
            for block in blocks:
                try:
                    self.queue.put_nowait(block)
                except queue.Full:
                    # 压缩帧无法任意丢弃后继续宣称有效；重建解码器并等待新参数集/关键帧。
                    self.reset_decoder.set()
                    with self.lock:
                        self.stats["overflow"] += 1
            with self.lock:
                self.stats.update({k: v - self.stats[k] for k, v in self.assembler.stats.items()})

    def _decode(self):
        codec = av.CodecContext.create("hevc", "r")
        codec.thread_count = 2
        while not self.stop_event.is_set():
            try:
                data = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.reset_decoder.is_set():
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        break
                codec = av.CodecContext.create("hevc", "r")
                codec.thread_count = 2
                self.reset_decoder.clear()
                with self.lock:
                    self.error = "解码积压，等待关键帧恢复"
                continue
            try:
                packets = codec.parse(data) if self.config.video_mode == "stream" else [av.Packet(data)]
                for packet in packets:
                    for frame in codec.decode(packet):
                        rgb = frame.to_ndarray(format="rgb24")
                        with self.lock:
                            self.latest = rgb
                            self.last_frame_time = time.monotonic()
                            self.stats["decoded"] += 1
                            self.error = "解码中" if not frame.is_corrupt else "画面可能损坏"
            except av.FFmpegError as exc:
                with self.lock:
                    self.stats["decode_errors"] += 1
                    self.error = f"等待可解码帧：{exc.strerror}"

    def snapshot(self):
        with self.lock:
            return self.latest, self.last_frame_time, dict(self.stats), self.error

    def stop(self):
        self.stop_event.set()
        if self.sock:
            self.sock.close()
        for thread in self.threads:
            thread.join(timeout=3)
