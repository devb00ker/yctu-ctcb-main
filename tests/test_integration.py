import socket
import time
import pytest
from terminal.config import Config
from terminal.protocol import EXAMPLES
from terminal.recording import State, Replay
from terminal.simulator import Simulator
from terminal.transport import Transport
from terminal.video import VideoReceiver


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def until(predicate, timeout=8):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("等待条件超时")


def test_real_mqtt_all_commands_reconnect_record_replay(tmp_path):
    p = port()
    sim = Simulator(port=p, video=False)
    state = State()
    cfg = Config(host="127.0.0.1", port=p)
    transport = Transport(cfg, state)
    log = tmp_path / "session.jsonl"
    try:
        sim.start()
        state.record(log)
        transport.start()
        until(lambda: state.snapshot()["ready"] and len(state.snapshot()["latest"]) == 23)
        for topic, data in EXAMPLES.items():
            transport.send(topic, data)
        until(lambda: len({r["topic"] for r in sim.commands}) == 9)
        with pytest.raises(ValueError):
            transport.send("GameStatus", {})
        sim.stop()
        until(lambda: not state.snapshot()["ready"])
        with pytest.raises(ConnectionError):
            transport.send("CommonCommand", {"cmd_type": 3})
        before = state.snapshot()["received"]
        sim = Simulator(port=p, video=False)
        sim.start()
        until(lambda: state.snapshot()["ready"] and state.snapshot()["received"] > before)
        time.sleep(0.2)
        assert not sim.commands, "重连后不应补发旧控制指令"
        transport.send("SentryCtrlCommand", {"command_id": 7})
        until(lambda: any(c["topic"] == "SentryCtrlCommand" for c in sim.commands))
    finally:
        transport.stop()
        sim.stop()
        state.record()
    playback = State()
    replay = Replay(log)
    replay.start -= 100
    try:
        while replay.tick(playback, "nested"):
            pass
        assert len(playback.latest) == 23
        assert playback.sent == 0
    finally:
        replay.close()


@pytest.mark.parametrize("mode", ["au", "stream"])
def test_real_hevc_over_udp(mode):
    p, vp = port(), port(socket.SOCK_DGRAM)
    config = Config(video_bind="127.0.0.1", video_source="127.0.0.1", video_port=vp, video_mode=mode)
    receiver = VideoReceiver(config)
    sim = Simulator(port=p, video_port=vp, width=320, height=180, fps=20)
    try:
        receiver.start()
        sim.start()
        until(lambda: receiver.snapshot()[2].get("decoded", 0) >= 10, 12)
        image, stamp, stats, error = receiver.snapshot()
        assert image.shape == (180,320,3)
        assert image.std() > 20
        assert time.monotonic()-stamp < 1
        assert stats["frames"] >= 10 and stats.get("overflow", 0) == 0
        assert sim.error is None
    finally:
        receiver.stop()
        sim.stop()
