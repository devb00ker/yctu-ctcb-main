import base64
import pytest
from terminal.config import Config
from terminal.protocol import UP, DOWN, encode, decode, interval, path_points
from terminal.simulator import samples


def test_registry_and_downlink_roundtrip():
    assert len(UP) == 9 and len(DOWN) == 23
    assert set(samples()) == set(DOWN)
    for topic, data in samples().items():
        assert decode(topic, encode(topic, data)) == data


def test_independent_wire_vectors_and_presence():
    # 手工根据 Protobuf tag/wire 规则确定字节，避免仅验证自己编码能解自己。
    assert encode("CommonCommand", {"cmd_type": 1, "param": 10}) == bytes.fromhex("0801100a")
    assert encode("CustomControl", {"data": "AQID"}) == bytes.fromhex("0a03010203")
    assert encode("KeyboardMouseControl", {"mouse_x": 0, "left_button_down": False}) == bytes.fromhex("08002000")
    assert decode("KeyboardMouseControl", bytes.fromhex("08002000")) == {"mouse_x": 0, "left_button_down": False}
    assert decode("GameStatus", bytes.fromhex("280430ac02")) == {"current_stage": 4, "stage_countdown_sec": 300}


@pytest.mark.parametrize("topic,data", [
    ("CustomControl", {"data": base64.b64encode(bytes(61)).decode()}),
    ("CustomByteBlock", {"data": base64.b64encode(bytes(301)).decode()}),
    ("KeyboardMouseControl", {"keyboard_value": 65536}),
    ("CommonCommand", {"cmd_type": 1, "param": 11}),
    ("CommonCommand", {"cmd_type": 7}),
    ("RobotPerformanceSelectionCommand", {"shooter": 3}),
    ("SentryCtrlCommand", {"command_id": 2}),
    ("MapClickCmd", {"robot_id": "AQ=="}),
    ("RobotPosition", {"x": "NaN"}),
    ("CustomControl", {}),
    ("CommonCommand", {"unknown": 1}),
])
def test_reject_invalid(topic, data):
    with pytest.raises(Exception):
        encode(topic, data)


def test_byte_boundaries_and_radar_modes():
    for topic, count in (("CustomControl", 60), ("CustomByteBlock", 300)):
        data = {"data": base64.b64encode(bytes(count)).decode()}
        assert decode(topic, encode(topic, data)) == data
    packed = {"radar_single_robot_info": [100, 200, 1]*8}
    payload = encode("RadarInfoToClient", packed, "flat")
    assert decode("RadarInfoToClient", payload, "flat") == packed
    with pytest.raises(Exception):
        decode("RadarInfoToClient", payload, "nested")


def test_intervals_and_path_ambiguity():
    assert interval("MapClickCmd") == 3
    assert interval("MapClickCmd", "commander") == 0.5
    assert interval("CustomControl") == 1/75
    data = dict(start_pos_x=10, start_pos_y=10, offset_x=[10, 10], offset_y=[0, 0])
    assert path_points(data, "origin") == [(1,1), (2,1), (2,1)]
    assert path_points(data, "previous") == [(1,1), (2,1), (3,1)]


def test_config_rejects_reserved_id():
    with pytest.raises(ValueError):
        Config(robot_id=2).validate()
