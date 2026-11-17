import base64
import json
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                              QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                              QMessageBox, QPushButton, QPlainTextEdit, QScrollArea, QSpinBox, QSplitter,
                              QTabWidget, QVBoxLayout, QWidget)
from .config import Config, ROOT, ROBOT_IDS
from .input import InputState, ZERO_INPUT
from .protocol import ALL, UP, DOWN, EXAMPLES, PERIODIC, path_points, radar_points
from .recording import State, Replay
from .transport import Transport
from .video import VideoReceiver

STYLE = """
QWidget { background: #101722; color: #dae4ef; font-family: 'Microsoft YaHei'; font-size: 13px; }
QMainWindow { background: #0b111b; }
QLabel#brand { font-size: 22px; font-weight: 700; color: #f1f5fa; }
QLabel#sub { color: #8c9eb6; font-size: 11px; }
QLabel#banner { background: #193a3b; color: #79e0c1; padding: 8px; border-radius: 5px; }
QFrame#card { background: #172231; border: 1px solid #29374a; border-radius: 8px; }
QFrame#card QLabel { background: transparent; }
QLabel#value { font-size: 28px; font-weight: 700; color: #e8f0f8; }
QPushButton { background: #25364a; border: 1px solid #3c526b; padding: 7px 12px; border-radius: 5px; }
QPushButton:hover { background: #334e68; }
QPushButton:disabled { color: #607086; border-color: #273546; }
QPushButton#primary { background: #166b60; border-color: #249984; }
QPushButton#stop { background: #6f2937; border-color: #9c4050; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { background: #0c1420; border: 1px solid #34445c; border-radius: 4px; padding: 5px; }
QTabBar::tab { padding: 9px 22px; color: #97a9bd; background: #121d2b; }
QTabBar::tab:selected { color: #71e0bf; background: #203347; }
QTabWidget::pane { border: 0; }
QCheckBox { spacing: 8px; }
QSplitter::handle { background: #253344; }
"""

_fonts_loaded = False


def prepare_fonts():
    global _fonts_loaded
    if _fonts_loaded:
        return
    if os.name == "nt":
        folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("msyh.ttc", "consola.ttf"):
            font = folder / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
    _fonts_loaded = True


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, slot, name=None):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(slot)
    return widget


class VideoPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 250)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.image = None
        self.age = 1e10
        self.caption = "等待 H.265 图传"
        self.captured = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#080e17"))
        if self.image:
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF((self.width()-size.width())/2, (self.height()-size.height())/2, size.width(), size.height())
            p.drawImage(target, self.image)
        else:
            p.setPen(QColor("#5c718b"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待图传接入\nUDP 3334 · HEVC / H.265")
        p.setPen(QPen(QColor("#6bf4d0"), 1))
        cx, cy = self.width()//2, self.height()//2
        p.drawLine(cx-22, cy, cx-7, cy)
        p.drawLine(cx+7, cy, cx+22, cy)
        p.drawLine(cx, cy-22, cx, cy-7)
        p.drawLine(cx, cy+7, cx, cy+22)
        p.fillRect(0, 0, self.width(), 30, QColor(8, 14, 23, 210))
        p.setPen(QColor("#8ce9c9") if self.age < 0.5 else QColor("#ffba72"))
        p.drawText(12, 20, self.caption)
        if self.image and self.age > 0.5:
            p.fillRect(0, self.height()-38, self.width(), 38, QColor(120, 35, 25, 220))
            p.setPen(QColor("#ffffff"))
            p.drawText(12, self.height()-14, f"画面已过期 {self.age:.1f}s · 请勿据此判断实时位置")
        if self.captured:
            p.setPen(QPen(QColor("#6bf4d0"), 3))
            p.drawRect(self.rect().adjusted(2, 2, -2, -2))


class MapPanel(QWidget):
    clicked = Signal(float, float)

    def __init__(self, config, demo=False):
        super().__init__()
        self.config = config
        self.demo = demo
        self.data = {}
        self.background = QImage(str(ROOT / config.map_image)) if config.map_image else QImage()
        self.setMinimumHeight(120)

    def plot_rect(self):
        scale = min((self.width()-32)/self.config.map_width_m, (self.height()-55)/self.config.map_height_m)
        width, height = self.config.map_width_m*scale, self.config.map_height_m*scale
        return QRectF((self.width()-width)/2, 31+(self.height()-55-height)/2, width, height)

    def pos(self, x, y):
        rect = self.plot_rect()
        return QPointF(rect.left()+x/self.config.map_width_m*rect.width(),
                       rect.bottom()-y/self.config.map_height_m*rect.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#111e2c"))
        rect = self.plot_rect()
        if not self.background.isNull():
            p.drawImage(rect, self.background)
        p.setPen(QColor(100, 130, 150, 80))
        for i in range(7):
            x = rect.left()+i*rect.width()/6
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for i in range(5):
            y = rect.top()+i*rect.height()/4
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        p.setPen(QColor("#89a0b8"))
        suffix = "演示坐标" if self.demo else "已校准" if self.config.map_calibrated else "坐标待校准"
        source = "STEP 场地" if not self.background.isNull() else "示意地图"
        p.drawText(12, 19, f"{source} · {self.config.map_width_m:g} × {self.config.map_height_m:g} m · {suffix}")
        p.drawText(12, self.height()-6, "(0,0)    X →  /  Y ↑    点击填入指令，不自动发送")
        if not self.config.map_calibrated and not self.demo:
            return
        p.save()
        p.setClipRect(rect)
        path = self.data.get("RobotPathPlanInfo", {})
        pts = path_points(path, self.config.path_mode) if path else []
        p.setPen(QPen(QColor("#51bcad"), 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(self.pos(*a), self.pos(*b))
        rows = radar_points(self.data.get("RadarInfoToClient", {}))
        for i, row in enumerate(rows):
            x, y = row.get("target_pos_x", 0), row.get("target_pos_y", 0)
            if x == 0 and y == 0:
                continue
            pos = self.pos(x/100, y/100)
            p.setBrush(QColor("#ee7c86" if i < 4 else "#76d5bc"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(pos, 5, 5)
            p.setPen(QColor("#142533") if not self.background.isNull() else QColor("#dce5ef"))
            p.drawText(pos+QPointF(8, 4), ("敌" if i < 4 else "己")+str([1,3,4,7][i%4]))
        own = self.data.get("RobotPosition", {})
        if "x" in own and "y" in own:
            p.setPen(QPen(QColor("#f7cd79"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self.pos(own["x"], own["y"]), 8, 8)
        p.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            rect = self.plot_rect()
            if not rect.contains(event.position()):
                return
            x = (event.position().x()-rect.left())/rect.width() * self.config.map_width_m
            y = (rect.bottom()-event.position().y())/rect.height() * self.config.map_height_m
            self.clicked.emit(x, y)


class MainWindow(QMainWindow):
    def __init__(self, config, demo=False):
        super().__init__()
        prepare_fonts()
        self.config = config
        self.demo = demo
        self.state = State()
        self.transport = None
        self.receiver = None
        self.replay = None
        self.inputs = InputState()
        self.recording = False
        self.image_stamp = 0
        self.generation = 0
        self.last_log = ""
        self.setWindowTitle("YCTU · 自定义终端 | RM2027")
        self.resize(1440, 960)
        self.setMinimumSize(1120, 820)
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 12)
        top = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(label("YCTU  /  自定义终端", "brand"))
        brand.addWidget(label("ROBOMASTER 2027  ·  RMUL  ·  COMMUNICATION V1.0.0", "sub"))
        self.mode_label = label("本地演示 / 模拟数据" if demo else "实时终端", "sub")
        brand.addWidget(self.mode_label)
        top.addLayout(brand)
        top.addStretch()
        self.host = QLineEdit(config.host)
        self.host.setMaximumWidth(180)
        self.robot = QComboBox()
        for rid in sorted(ROBOT_IDS):
            role = "重装" if rid % 100 == 1 else "哨兵" if rid % 100 == 7 else "步兵"
            self.robot.addItem(f"{'红' if rid < 100 else '蓝'}{rid%100} · {role}", rid)
        self.robot.setCurrentIndex(self.robot.findData(config.robot_id))
        top.addWidget(label("服务器"))
        top.addWidget(self.host)
        top.addWidget(label("机器人 ID"))
        top.addWidget(self.robot)
        self.connect_button = button("连接", self.toggle_connection, "primary")
        top.addWidget(self.connect_button)
        top.addWidget(button("断开 / 释放", self.disconnect_all, "stop"))
        layout.addLayout(top)
        self.banner = label("本地演示 · 所有战况为模拟数据" if demo else "实时模式 · 未连接官方服务器", "banner")
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._dashboard()
        self._protocol_tab()
        self._settings_tab()
        self.robot.currentIndexChanged.connect(self.update_role)
        self.update_role()
        bottom = QHBoxLayout()
        self.link_label = label("未连接", "sub")
        bottom.addWidget(self.link_label, 1)
        self.record_button = button("开始记录", self.toggle_record)
        bottom.addWidget(self.record_button)
        bottom.addWidget(button("回放 JSONL", self.open_replay))
        bottom.addWidget(button("结束回放", self.stop_replay))
        layout.addLayout(bottom)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(50)
        self.control_timer = QTimer(self)
        self.control_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.control_timer.timeout.connect(self.control_tick)
        self.control_timer.start(2)
        self.tabs.currentChanged.connect(lambda _: self.disarm())
        QApplication.instance().installEventFilter(self)

    def _dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        cards = QHBoxLayout()
        self.metrics = {}
        for key, title in (("time", "比赛剩余"), ("score", "红方 : 蓝方"), ("hp", "自身血量"),
                           ("heat", "射击热量"), ("ammo", "剩余弹量"), ("economy", "当前经济")):
            card = QFrame()
            card.setObjectName("card")
            col = QVBoxLayout(card)
            col.addWidget(label(title, "sub"))
            value = label("—", "value")
            col.addWidget(value)
            cards.addWidget(card)
            self.metrics[key] = value
        layout.addLayout(cards)
        self.summary = label("阶段 —   |   基地 — / —   |   控制度 — / —   |   缓冲能量 —", "sub")
        layout.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        self.video = VideoPanel()
        ll.addWidget(self.video, 1)
        controls = QHBoxLayout()
        self.arm_button = button("启用键鼠 · Esc 释放", self.arm, "primary")
        controls.addWidget(self.arm_button)
        controls.addWidget(button("释放键鼠", self.disarm, "stop"))
        self.input_label = label("键鼠未启用", "sub")
        controls.addWidget(self.input_label, 1)
        ll.addLayout(controls)
        self.video_stats = label("UDP 未启动", "sub")
        ll.addWidget(self.video_stats)
        right = QWidget()
        right.setMinimumWidth(310)
        right.setMaximumWidth(470)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.addWidget(label("常用操作"))
        self.command = QComboBox()
        self.command.addItems(["兑换17mm弹量", "兑换42mm弹量", "确认免费复活", "兑换立即复活", "远程补弹", "远程回血"])
        self.param = QSpinBox()
        self.param.setRange(0, 65535)
        self.param.setValue(10)
        form = QFormLayout()
        form.addRow("指令", self.command)
        form.addRow("参数", self.param)
        rl.addLayout(form)
        rl.addWidget(button("发送常用指令", self.send_common))
        self.shooter, self.chassis = QComboBox(), QComboBox()
        self.shooter.addItems(["冷却优先", "爆发优先"])
        self.chassis.addItems(["血量优先", "功率优先"])
        form2 = QFormLayout()
        form2.addRow("发射机构", self.shooter)
        form2.addRow("底盘", self.chassis)
        rl.addLayout(form2)
        rl.addWidget(button("选择性能体系", lambda: self.send("RobotPerformanceSelectionCommand",
                                  dict(shooter=self.shooter.currentIndex()+1, chassis=self.chassis.currentIndex()+1))))
        self.sentry = QComboBox()
        self.sentry.addItems(["进攻姿态", "防御姿态", "移动姿态", "强化进攻", "强化防御", "强化移动"])
        self.sentry_box = QWidget()
        sentry_layout = QVBoxLayout(self.sentry_box)
        sentry_layout.setContentsMargins(0, 0, 0, 0)
        posture_row = QHBoxLayout()
        posture_row.addWidget(self.sentry, 1)
        posture_row.addWidget(button("发送姿态", lambda: self.send("SentryCtrlCommand", dict(command_id=self.sentry.currentIndex()+7))))
        sentry_layout.addLayout(posture_row)
        self.sentry_control = QComboBox()
        self.sentry_control.addItems(["自主控制", "半自动控制"])
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.sentry_control, 1)
        mode_row.addWidget(button("选择控制方式", lambda: self.send("RobotPerformanceSelectionCommand", dict(sentry_control=self.sentry_control.currentIndex()))))
        sentry_layout.addLayout(mode_row)
        rl.addWidget(self.sentry_box)
        self.assembly_box = QWidget()
        assembly_layout = QVBoxLayout(self.assembly_box)
        assembly_layout.setContentsMargins(0, 0, 0, 0)
        assembly_row = QHBoxLayout()
        self.assembly_operation = QComboBox()
        self.assembly_operation.addItems(["开始兑换", "确认装配", "取消装配"])
        self.assembly_difficulty = QSpinBox()
        self.assembly_difficulty.setRange(1, 4)
        self.assembly_difficulty.setPrefix("难度 ")
        assembly_row.addWidget(self.assembly_operation)
        assembly_row.addWidget(self.assembly_difficulty)
        assembly_layout.addLayout(assembly_row)
        assembly_layout.addWidget(button("发送重装装配指令", lambda: self.send("AssemblyCommand", dict(operation=self.assembly_operation.currentIndex(), difficulty=self.assembly_difficulty.value()))))
        rl.addWidget(self.assembly_box)
        self.map = MapPanel(self.config, self.demo)
        self.map.clicked.connect(self.map_clicked)
        rl.addWidget(self.map, 1)
        split.addWidget(left)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setMinimumWidth(330)
        right_scroll.setWidget(right)
        split.addWidget(right_scroll)
        split.setSizes([950, 360])
        layout.addWidget(split, 1)
        self.modules = label("模块状态：等待 RobotModuleStatus", "sub")
        self.modules.setWordWrap(True)
        layout.addWidget(self.modules)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(95)
        layout.addWidget(self.log)
        self.tabs.addTab(page, "操作台")

    def _protocol_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        tx = QVBoxLayout()
        tx.addWidget(label("上行 · 9 条指令 / JSON 编辑"))
        self.topic = QComboBox()
        self.topic.addItems(UP)
        tx.addWidget(self.topic)
        self.fields = label("", "sub")
        self.fields.setWordWrap(True)
        tx.addWidget(self.fields)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 11))
        tx.addWidget(self.editor, 1)
        tx.addWidget(label("bytes 使用 Base64；十六进制自定义数据可从下方发送。", "sub"))
        tx.addWidget(button("校验并发送一次", self.send_editor, "primary"))
        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("CustomControl 十六进制，例如 01 02 03（≤60B）")
        tx.addWidget(self.hex_input)
        tx.addWidget(button("发送自定义字节", self.send_hex))
        rx = QVBoxLayout()
        rx.addWidget(label("下行 · 23 条状态 / 原始数据"))
        self.rx_topic = QComboBox()
        self.rx_topic.addItems(DOWN)
        rx.addWidget(self.rx_topic)
        self.rx_age = label("等待数据", "sub")
        rx.addWidget(self.rx_age)
        self.rx_view = QPlainTextEdit()
        self.rx_view.setReadOnly(True)
        self.rx_view.setFont(QFont("Consolas", 11))
        rx.addWidget(self.rx_view, 1)
        layout.addLayout(tx, 1)
        layout.addLayout(rx, 1)
        self.topic.currentTextChanged.connect(self.topic_changed)
        self.topic_changed(self.topic.currentText())
        self.tabs.addTab(page, "协议调试")

    def _settings_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        text = label("连接设置修改后需断开并重新连接。底图来自 STEP 的12×8m地坪；校准裁判坐标原点、方向和单位后将 map_calibrated 改为 true。\n"
                     "radar_format: nested / flat（官方定义待确认）；video_mode: au / stream；path_mode: origin / previous。\n"
                     "map_role: operator（3秒）/ commander（0.5秒）；鼠标 Y 方向和比例需与电控联调。", "sub")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.config_editor = QPlainTextEdit(json.dumps(asdict(self.config), ensure_ascii=False, indent=2))
        self.config_editor.setFont(QFont("Consolas", 11))
        layout.addWidget(self.config_editor)
        layout.addWidget(button("校验并保存配置", self.save_config, "primary"))
        self.tabs.addTab(page, "连接与配置")

    def topic_changed(self, topic):
        self.editor.setPlainText(json.dumps(EXAMPLES[topic], ensure_ascii=False, indent=2))
        self.fields.setText("字段：" + " · ".join(f"{f.name} #{f.number}" for f in ALL[topic].DESCRIPTOR.fields))

    def update_role(self):
        rid = self.robot.currentData()
        self.sentry_box.setVisible(rid % 100 == 7)
        self.assembly_box.setVisible(rid % 100 == 1)

    def error(self, exc):
        self.state.log(str(exc))
        self.banner.setText(f"操作未完成：{exc}")

    def toggle_connection(self):
        if self.transport:
            self.disconnect_all()
            return
        self.stop_replay()
        try:
            self.config.host = self.host.text().strip()
            if self.demo and self.config.host != "127.0.0.1":
                raise ValueError("本地演示只连接 127.0.0.1；连接官方设备请使用 start.bat 启动正式模式")
            self.config.robot_id = self.robot.currentData()
            self.config.validate()
            self.state.latest.clear()
            self.transport = Transport(self.config, self.state)
            self.transport.start()
            self.receiver = VideoReceiver(self.config)
            try:
                self.receiver.start()
            except OSError as exc:
                self.receiver = None
                self.error(f"图传端口打开失败，MQTT 继续连接：{exc}")
            self.connect_button.setText("断开")
            self.host.setEnabled(False)
            self.robot.setEnabled(False)
        except Exception as exc:
            self.error(exc)

    def disconnect_all(self):
        self.disarm()
        if self.transport:
            # 全零释放可能受 75Hz 限频，等待释放定时器后再关闭网络。
            transport, self.transport = self.transport, None
            QTimer.singleShot(25, transport.stop)
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
        self.connect_button.setText("连接")
        self.host.setEnabled(True)
        self.robot.setEnabled(True)
        self.video.age = 1e10
        self.video.image = None
        self.state.status("已断开")

    def send(self, topic, data):
        try:
            if self.replay or not self.transport:
                raise ConnectionError("请先连接实时服务器；回放模式禁止发送")
            self.transport.send(topic, data)
            if topic != "KeyboardMouseControl":
                self.banner.setText(f"已提交 {topic}；执行结果请查看下行状态")
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def send_common(self):
        self.send("CommonCommand", dict(cmd_type=self.command.currentIndex()+1, param=self.param.value()))

    def send_editor(self):
        try:
            if self.topic.currentText() == "KeyboardMouseControl" and self.inputs.active:
                raise ValueError("请先释放实时键鼠，再调试键鼠消息")
            topic = self.topic.currentText()
            if self.send(topic, json.loads(self.editor.toPlainText())) and topic == "KeyboardMouseControl":
                transport = self.transport
                def release_debug():
                    try:
                        transport.send("KeyboardMouseControl", ZERO_INPUT)
                    except (ValueError, ConnectionError, BufferError) as exc:
                        self.state.log(f"调试键鼠释放失败：{exc}")
                QTimer.singleShot(20, release_debug)
        except Exception as exc:
            self.error(exc)

    def send_hex(self):
        try:
            data = bytes.fromhex(self.hex_input.text())
            self.send("CustomControl", dict(data=base64.b64encode(data).decode()))
        except ValueError as exc:
            self.error(exc)

    def map_clicked(self, x, y):
        if self.replay:
            self.banner.setText("离线回放仅查看地图；结束回放后再测试地图指令")
            return
        if not self.demo and not self.config.map_calibrated:
            self.banner.setText("地图坐标尚未校准，暂不能生成地图指令；图传、状态显示和键鼠功能不受影响")
            return
        data = EXAMPLES["MapClickCmd"].copy()
        data.update(map_x=x*self.config.map_click_units_per_m, map_y=y*self.config.map_click_units_per_m,
                    robot_id=base64.b64encode(bytes([self.config.robot_id])+bytes(6)).decode())
        self.topic.setCurrentText("MapClickCmd")
        self.editor.setPlainText(json.dumps(data, indent=2))
        self.tabs.setCurrentIndex(1)
        if self.demo:
            self.banner.setText("演示地图坐标已填入；点击“校验并发送一次”可发送到本地模拟器，实机地图仍需校准")

    def save_config(self):
        if self.transport or self.replay:
            self.error("请先断开连接并结束回放，再保存配置")
            return
        try:
            self.config = Config(**json.loads(self.config_editor.toPlainText())).validate()
            self.config.save()
            self.host.setText(self.config.host)
            self.robot.setCurrentIndex(self.robot.findData(self.config.robot_id))
            self.map.config = self.config
            self.map.background = QImage(str(ROOT / self.config.map_image)) if self.config.map_image else QImage()
            self.banner.setText("配置已保存到 config/local.json")
        except Exception as exc:
            self.error(exc)

    def arm(self):
        if self.replay or not self.transport or not self.state.snapshot()["ready"]:
            self.error("连接就绪后才能启用键鼠")
            return
        self.inputs.clear()
        self.inputs.active = True
        self.video.captured = True
        self.video.setFocus()
        self.video.setCursor(Qt.CursorShape.BlankCursor)
        self.video.grabMouse()
        self.video.grabKeyboard()
        self.center_cursor()
        self.input_label.setText("键鼠已启用 · Esc/失焦释放")

    def center_cursor(self):
        QCursor.setPos(self.video.mapToGlobal(self.video.rect().center()))

    def disarm(self):
        was_active = self.inputs.active
        self.inputs.clear()
        self.video.captured = False
        self.video.unsetCursor()
        if was_active:
            self.video.releaseMouse()
            self.video.releaseKeyboard()
        self.input_label.setText("键鼠未启用")
        if was_active and self.transport:
            transport = self.transport
            def release():
                try:
                    transport.send("KeyboardMouseControl", ZERO_INPUT)
                except (ConnectionError, ValueError, BufferError) as exc:
                    self.state.log(f"全零释放未送达：{exc}；机器人侧应有输入超时保护")
            QTimer.singleShot(16, release)

    def control_tick(self):
        if self.inputs.active:
            try:
                self.inputs.tick(self.transport, self.config)
            except Exception as exc:
                self.disarm()
                self.error(exc)

    def eventFilter(self, obj, event):
        if not self.inputs.active:
            return super().eventFilter(obj, event)
        if event.type() in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
            self.disarm()
            return False
        keys = [Qt.Key.Key_W, Qt.Key.Key_S, Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_Shift,
                Qt.Key.Key_Control, Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_R, Qt.Key.Key_F,
                Qt.Key.Key_G, Qt.Key.Key_Z, Qt.Key.Key_X, Qt.Key.Key_C, Qt.Key.Key_V, Qt.Key.Key_B]
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if event.key() == Qt.Key.Key_Escape:
                self.disarm()
                return True
            if event.key() in keys and not event.isAutoRepeat():
                index = keys.index(event.key())
                if event.type() == QEvent.Type.KeyPress:
                    self.inputs.keys.add(index)
                else:
                    self.inputs.keys.discard(index)
            return True
        if obj is self.video:
            if event.type() == QEvent.Type.MouseMove:
                delta = event.globalPosition().toPoint() - self.video.mapToGlobal(self.video.rect().center())
                self.inputs.dx += delta.x()
                self.inputs.dy += delta.y()
                if not delta.isNull():
                    self.center_cursor()
                return True
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                value = event.button().value
                if event.type() == QEvent.Type.MouseButtonPress:
                    self.inputs.buttons.add(value)
                else:
                    self.inputs.buttons.discard(value)
                return True
            if event.type() == QEvent.Type.Wheel:
                self.inputs.dz += event.angleDelta().y()
                return True
        if event.type() == QEvent.Type.MouseButtonPress:
            self.disarm()
        return super().eventFilter(obj, event)

    def toggle_record(self):
        if self.replay:
            self.error("回放期间不录制")
            return
        self.recording = not self.recording
        try:
            path = ROOT / "output" / f"session-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
            self.state.record(path if self.recording else None)
            self.record_button.setText("停止记录" if self.recording else "开始记录")
            self.banner.setText(f"记录中：{path}" if self.recording else "记录已保存")
        except OSError as exc:
            self.recording = False
            self.error(exc)

    def open_replay(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择记录", str(ROOT / "output"), "记录 (*.jsonl)")
        if path:
            self.load_replay(path)

    def load_replay(self, path):
        self.disconnect_all()
        self.stop_replay()
        self.state.record()
        # 正在关闭的网络线程持有旧 State，避免迟到消息混入离线回放。
        self.state = State()
        self.state.status("离线回放")
        self.recording = False
        self.record_button.setText("开始记录")
        try:
            self.replay = Replay(path)
            self.mode_label.setText("离线回放 / 禁止发送")
            self.state.latest.clear()
            self.banner.setText(f"离线回放 · 禁止发送 · {Path(path).name}")
        except Exception as exc:
            self.error(exc)

    def stop_replay(self):
        if self.replay:
            self.replay.close()
            self.replay = None
            self.state.latest.clear()
            self.banner.setText("回放已结束 · 未连接")
            self.mode_label.setText("本地演示 / 模拟数据" if self.demo else "实时终端")

    def refresh(self):
        if self.replay:
            try:
                if not self.replay.tick(self.state, self.config.radar_format):
                    self.banner.setText("离线回放完成 · 禁止发送 · 可结束回放后重新连接")
            except Exception as exc:
                self.stop_replay()
                self.error(f"回放失败：{exc}")
        snapshot = self.state.snapshot()
        if self.generation != snapshot["generation"]:
            self.generation = snapshot["generation"]
            self.disarm()
        now = time.monotonic()
        latest = snapshot["latest"]
        def value(topic, key):
            row = latest.get(topic)
            if not row or key not in row["data"]:
                return "—"
            result = row["data"][key]
            stale = not self.replay and (not snapshot["ready"] or now-row["monotonic"] > max(0.5, 3/max(1, PERIODIC[topic])))
            return str(result) + (" *" if stale else "")
        self.metrics["time"].setText(value("GameStatus", "stage_countdown_sec") + " s")
        self.metrics["score"].setText(value("GameStatus", "red_score")+" : "+value("GameStatus", "blue_score"))
        for key, topic, field in (("hp", "RobotDynamicStatus", "current_health"),
                                  ("heat", "RobotDynamicStatus", "current_heat"),
                                  ("ammo", "RobotDynamicStatus", "remaining_ammo"),
                                  ("economy", "GlobalLogisticsStatus", "remaining_economy")):
            self.metrics[key].setText(value(topic, field))
        stage = latest.get("GameStatus", {}).get("data", {}).get("current_stage")
        stage_name = {0:"未开始",1:"准备",2:"自检",3:"倒计时",4:"比赛中",5:"结算"}.get(stage,"未知")
        pause = latest.get("GameStatus", {}).get("data", {}).get("is_paused", False)
        self.summary.setText(f"{stage_name}{' · 已暂停' if pause else ''}   |   基地 {value('GlobalUnitStatus','base_health')} / {value('GlobalUnitStatus','enemy_base_health')}"
                             f"   |   控制度 {value('GlobalUnitStatus','total_CP_ally')} / {value('GlobalUnitStatus','total_CP_enemy')}"
                             f"   |   缓冲 {value('RobotDynamicStatus','current_buffer_energy')} J   |   带 * 的数值表示已过期")
        # 地图只展示近期数据，避免把旧位置误当实时。
        self.map.data = {n:r["data"] for n,r in latest.items() if self.replay or snapshot["ready"] and now-r["monotonic"] < 3}
        self.map.update()
        module_row = latest.get("RobotModuleStatus")
        if module_row:
            names = {"power_manager":"电源", "rfid":"RFID", "light_strip":"灯条", "small_shooter":"17mm测速",
                     "big_shooter":"42mm测速", "uwb":"定位", "armor":"装甲", "video_transmission":"图传",
                     "capacitor":"电容", "main_controller":"主控", "modular_42mm":"42mm权限"}
            self.modules.setText("模块  " + "   ".join(f"{names.get(k,k)}:{v}" for k,v in module_row["data"].items()) +
                                 ("  [已过期]" if not snapshot["ready"] or now-module_row["monotonic"] > 3 else "  （0离线 / 1在线 / 2安装异常；权限0/1）"))
        else:
            self.modules.setText("模块状态：等待 RobotModuleStatus")
        if self.receiver:
            rgb, stamp, stats, error = self.receiver.snapshot()
            if rgb is not None and stamp != self.image_stamp:
                self.image_stamp = stamp
                self.video.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
            self.video.age = now-stamp if stamp else 1e10
            self.video.caption = f"{'本地演示' if self.demo else '实时图传'} · {error}"
            self.video_stats.setText("  |  ".join(f"{n} {stats.get(k,0)}" for k,n in
                                                   (("packets","数据报"),("frames","重组"),("decoded","解码"),
                                                    ("expired","过期"),("duplicate","重复"),("decode_errors","解码错误"),("overflow","积压"))))
        else:
            self.video.caption = "UDP 未启动"
        self.video.update()
        self.link_label.setText(f"{snapshot['link']}   |   RX {snapshot['received']}   TX {snapshot['sent']}   拒收 {snapshot['rejected']}")
        row = latest.get(self.rx_topic.currentText())
        if row:
            self.rx_age.setText(f"更新于 {now-row['monotonic']:.1f}s 前 · {len(row['hex'])//2} B")
            content = json.dumps(row["data"], ensure_ascii=False, indent=2) + "\n\n原始 HEX\n" + row["hex"]
        else:
            self.rx_age.setText("等待数据")
            content = "暂无数据"
        if self.rx_view.toPlainText() != content:
            self.rx_view.setPlainText(content)
        logs = "\n".join(f"{datetime.fromtimestamp(t):%H:%M:%S}  {s}" for t,s in snapshot["events"][-30:])
        if logs != self.last_log:
            self.last_log = logs
            self.log.setPlainText(logs)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def closeEvent(self, event):
        self.disarm()
        self.timer.stop()
        self.control_timer.stop()
        if self.transport:
            # 退出前直接尝试释放；网络失效仍需电控超时保护。
            time.sleep(0.015)
            try:
                self.transport.send("KeyboardMouseControl", ZERO_INPUT)
            except (ValueError, ConnectionError, BufferError):
                pass
            self.transport.stop()
        if self.receiver:
            self.receiver.stop()
        if self.replay:
            self.replay.close()
        self.state.record()
        QApplication.instance().removeEventFilter(self)
        event.accept()
