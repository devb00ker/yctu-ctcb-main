"""协议边界：由官方 protobuf 库处理 wire，保留 optional presence。"""
import math
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from .generated import rm2027_client_up_pb2 as up
from .generated import rm2027_client_down_pb2 as down
from .generated import rm2027_radar_flat_pb2 as flat

UP = {n: getattr(up, n) for n in up.DESCRIPTOR.message_types_by_name}
DOWN = {n: getattr(down, n) for n in down.DESCRIPTOR.message_types_by_name}
ALL = UP | DOWN
PERIODIC = {n: 1 for n in DOWN}
PERIODIC.update(GameStatus=5, RobotDynamicStatus=10, CustomByteBlock=50)
for _name in ("Event", "PenaltyInfo", "RobotPathPlanInfo", "MapClickInfo", "RadarInfoToClient", "SentryCtrlResult"):
    PERIODIC[_name] = 0

EXAMPLES = {
    "KeyboardMouseControl": dict(mouse_x=0, mouse_y=0, mouse_z=0, left_button_down=False,
                                 right_button_down=False, keyboard_value=0, mid_button_down=False),
    "CustomControl": {"data": ""},
    "MapClickCmd": dict(is_send_all=0, robot_id="AwAAAAAAAA==", mode=3, enemy_id=0,
                        ascii=0, type=1, map_x=0, map_y=0),
    "AssemblyCommand": dict(operation=0, difficulty=1),
    "RobotPerformanceSelectionCommand": dict(shooter=1, chassis=1),
    "CommonCommand": dict(cmd_type=1, param=10),
    "RuneActivateCommand": dict(activate=1),
    "DartCommand": dict(target_id=1, open=False, launch_confirm=False),
    "SentryCtrlCommand": dict(command_id=9),
}


def interval(topic, role="operator"):
    if topic in ("KeyboardMouseControl", "CustomControl"):
        return 1 / 75
    if topic == "MapClickCmd":
        return 0.5 if role == "commander" else 3.0
    return 0.1


def validate(msg):
    name = msg.DESCRIPTOR.name
    for field, value in msg.ListFields():
        if field.type in (field.TYPE_FLOAT, field.TYPE_DOUBLE) and not math.isfinite(value):
            raise ValueError(f"{field.name} 必须为有限数")

    def allowed(key, values):
        if msg.HasField(key) and getattr(msg, key) not in values:
            raise ValueError(f"{key} 不在允许范围 {list(values)}")

    if name in ("CustomControl", "CustomByteBlock"):
        limit = 60 if name == "CustomControl" else 300
        if len(msg.data) > limit:
            raise ValueError(f"{name}.data 最多 {limit} 字节")
    elif name == "KeyboardMouseControl":
        if msg.keyboard_value > 65535:
            raise ValueError("键盘仅允许 bit0–15")
    elif name in ("MapClickCmd", "MapClickInfo"):
        if len(msg.robot_id) != 7:
            raise ValueError("robot_id 必须固定 7 字节")
        allowed("is_send_all", (0, 1, 2))
        allowed("mode", (1, 2, 3, 4))
        allowed("type", (1, 2))
        if msg.mode == 4 and chr(msg.ascii) not in "CDEFGHIJKLNOQRSTUVWXYZ":
            raise ValueError("自定义图标仅允许 C–L、N、O、Q–Z")
    elif name == "CommonCommand":
        allowed("cmd_type", range(1, 7))
        if not msg.HasField("cmd_type"):
            raise ValueError("必须填写 cmd_type")
        if msg.cmd_type == 1 and (msg.param == 0 or msg.param % 10):
            raise ValueError("17mm 兑换数量必须为正的 10 的倍数")
    elif name == "RobotPerformanceSelectionCommand":
        allowed("shooter", (1, 2))
        allowed("chassis", (1, 2))
        allowed("sentry_control", (0, 1))
    elif name == "AssemblyCommand":
        allowed("operation", (0, 1, 2))
        allowed("difficulty", (1, 2, 3, 4))
    elif name == "RuneActivateCommand":
        allowed("activate", (1,))
    elif name == "DartCommand":
        allowed("target_id", range(1, 6))
    elif name == "SentryCtrlCommand":
        allowed("command_id", (1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12))
    elif name == "GlobalUnitStatus":
        if msg.robot_health and len(msg.robot_health) != 8:
            raise ValueError("robot_health 必须为 8 项")
    elif name == "RobotPathPlanInfo":
        if len(msg.offset_x) != 49 or len(msg.offset_y) != 49:
            raise ValueError("路径必须有 49 组增量")
        if any(not -128 <= v <= 127 for v in (*msg.offset_x, *msg.offset_y)):
            raise ValueError("路径增量应在 -128–127")
    elif name == "GlobalSpecialMechanism":
        if len(msg.mechanism_id) != len(msg.mechanism_time_sec):
            raise ValueError("机制与时间数组必须等长")
    elif name == "RadarInfoToClient":
        count = len(msg.radar_single_robot_info)
        expected = 24 if msg.DESCRIPTOR.full_name.startswith("rm_radar_flat.") else 8
        if count != expected:
            raise ValueError(f"雷达需要 {expected} 项，请核对 radar_format 配置")
        if expected == 8 and any(r.is_high_light > 2 for r in msg.radar_single_robot_info):
            raise ValueError("雷达标识应为 0/1/2")
        if expected == 24 and any(v > 2 for v in msg.radar_single_robot_info[2::3]):
            raise ValueError("雷达标识应为 0/1/2")
    return msg


def encode(topic, data, radar_format="nested"):
    cls = flat.RadarInfoToClient if topic == "RadarInfoToClient" and radar_format == "flat" else ALL[topic]
    msg = json_format.ParseDict(data, cls(), ignore_unknown_fields=False)
    validate(msg)
    if topic in UP and not msg.ListFields():
        raise ValueError("上行消息不能缺少全部字段")
    return msg.SerializeToString()


def decode(topic, payload, radar_format="nested"):
    if len(payload) > 65536:
        raise ValueError("MQTT 消息超过本程序 64KiB 接收上限")
    cls = flat.RadarInfoToClient if topic == "RadarInfoToClient" and radar_format == "flat" else ALL[topic]
    msg = cls.FromString(payload)
    validate(msg)
    return json_format.MessageToDict(msg, preserving_proto_field_name=True)


def radar_points(data):
    rows = data.get("radar_single_robot_info", [])
    if rows and isinstance(rows[0], int):
        return [dict(target_pos_x=rows[i], target_pos_y=rows[i+1], is_high_light=rows[i+2])
                for i in range(0, len(rows), 3)]
    return rows


def path_points(data, mode="origin"):
    start = (data.get("start_pos_x", 0) / 10, data.get("start_pos_y", 0) / 10)
    points = [start]
    for dx, dy in zip(data.get("offset_x", []), data.get("offset_y", [])):
        base = points[-1] if mode == "previous" else start
        points.append((base[0] + dx / 10, base[1] + dy / 10))
    return points
