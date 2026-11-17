import ipaddress
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROBOT_IDS = {1, 3, 4, 5, 7, 101, 103, 104, 105, 107}


@dataclass
class Config:
    host: str = "192.168.12.1"
    port: int = 3333
    robot_id: int = 3
    video_bind: str = "0.0.0.0"
    video_port: int = 3334
    video_source: str = "192.168.12.1"
    video_mode: str = "au"
    radar_format: str = "nested"
    map_role: str = "operator"
    map_width_m: float = 12.0
    map_height_m: float = 8.0
    map_calibrated: bool = False
    map_image: str = "assets/maps/rmul2027-top.png"
    map_click_units_per_m: float = 1.0
    path_mode: str = "origin"
    mouse_scale: float = 1.0
    mouse_y_sign: int = -1
    control_hz: int = 75

    def validate(self):
        if not self.host.strip() or self.robot_id not in ROBOT_IDS:
            raise ValueError("服务器不能为空；RMUL 操作台只接受步兵、哨兵、重装的机器人 ID")
        for value in (self.port, self.video_port):
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError("端口必须为 1–65535")
        ipaddress.IPv4Address(self.video_bind)
        if self.video_source:
            ipaddress.IPv4Address(self.video_source)
        for value, choices in ((self.video_mode, {"au", "stream"}),
                               (self.radar_format, {"nested", "flat"}),
                               (self.map_role, {"operator", "commander"}),
                               (self.path_mode, {"origin", "previous"})):
            if value not in choices:
                raise ValueError(f"非法配置选项：{value}")
        import math
        if not all(math.isfinite(v) and v > 0 for v in
                   (self.map_width_m, self.map_height_m, self.map_click_units_per_m, self.mouse_scale)):
            raise ValueError("地图尺寸、单位换算和鼠标比例必须是正有限数")
        if type(self.control_hz) is not int or not 1 <= self.control_hz <= 75:
            raise ValueError("control_hz 必须为 1–75")
        if self.mouse_y_sign not in (-1, 1) or type(self.map_calibrated) is not bool:
            raise ValueError("mouse_y_sign 必须为 ±1，map_calibrated 必须为布尔值")
        if self.map_image and not (ROOT / self.map_image).is_file():
            raise ValueError(f"地图图片不存在：{self.map_image}")
        return self

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else ROOT / "config" / "local.json"
        if not path.exists():
            path = ROOT / "config" / "default.json"
        return cls(**json.loads(path.read_text(encoding="utf-8"))).validate()

    def save(self, path=None):
        self.validate()
        path = Path(path) if path else ROOT / "config" / "local.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
