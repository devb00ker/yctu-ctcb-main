import os
import json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import socket
import time
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from terminal.config import Config, ROOT
from terminal.ui import MainWindow
from terminal.simulator import Simulator


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]


def pump(app, condition, seconds=10):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("GUI 条件超时")


def test_dashboard_commands_input_release_and_screenshots():
    app = QApplication.instance() or QApplication([])
    p, vp = free_port(), free_port(socket.SOCK_DGRAM)
    sim = Simulator(port=p, video_port=vp, width=640, height=360, fps=20)
    config = Config(host="127.0.0.1", port=p, video_port=vp, video_source="127.0.0.1")
    window = MainWindow(config, demo=True)
    try:
        sim.start()
        window.show()
        window.toggle_connection()
        pump(app, lambda: window.metrics["hp"].text() == "380" and window.metrics["economy"].text() == "900" and window.video.image is not None)
        assert window.metrics["economy"].text() == "900"
        window.send_common()
        pump(app, lambda: any(c["topic"] == "CommonCommand" for c in sim.commands))
        assert not window.config.map_calibrated
        QTest.mouseClick(window.map, Qt.MouseButton.LeftButton, pos=window.map.pos(2,3).toPoint())
        assert window.tabs.currentIndex() == 1
        assert window.topic.currentText() == "MapClickCmd"
        assert not any(c["topic"] == "MapClickCmd" for c in sim.commands)
        expected = json.loads(window.editor.toPlainText())
        window.send_editor()
        pump(app, lambda: any(c["topic"] == "MapClickCmd" for c in sim.commands))
        actual = next(c["data"] for c in sim.commands if c["topic"] == "MapClickCmd")
        assert abs(actual["map_x"]-expected["map_x"]) < 1e-5
        assert abs(actual["map_y"]-expected["map_y"]) < 1e-5
        assert not window.config.map_calibrated
        window.tabs.setCurrentIndex(0)
        window.arm()
        app.sendEvent(window.video, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier))
        pump(app, lambda: any(c["topic"] == "KeyboardMouseControl" and c["data"].get("keyboard_value") == 1 for c in sim.commands))
        app.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
        assert not window.inputs.active and not window.inputs.keys
        pump(app, lambda: sim.commands[-1]["topic"] == "KeyboardMouseControl" and sim.commands[-1]["data"].get("keyboard_value") == 0)
        out = ROOT / "output"
        out.mkdir(exist_ok=True)
        window.refresh()
        assert window.grab().save(str(out / "dashboard.png"))
        window.tabs.setCurrentIndex(1)
        window.topic.setCurrentText("CustomControl")
        window.rx_topic.setCurrentText("RobotDynamicStatus")
        window.refresh()
        app.processEvents()
        assert window.grab().save(str(out / "protocol.png"))
        window.tabs.setCurrentIndex(2)
        app.processEvents()
        assert window.grab().save(str(out / "settings.png"))
    finally:
        window.close()
        app.processEvents()
        sim.stop()


def test_rmul_roles_map_aspect_and_calibration_gate():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Config())
    try:
        window.show()
        app.processEvents()
        assert not window.map.background.isNull()
        rect = window.map.plot_rect()
        assert abs(rect.width()/rect.height() - 1.5) < 1e-6
        for rid, heavy, sentry in ((3,False,False),(1,True,False),(7,False,True)):
            window.robot.setCurrentIndex(window.robot.findData(rid))
            app.processEvents()
            assert window.assembly_box.isVisible() == heavy
            assert window.sentry_box.isVisible() == sentry
            assert window.map.geometry().bottom() <= window.map.parentWidget().height()
            assert window.grab().save(str(ROOT / "output" / f"role-{rid}.png"))
        window.map_clicked(2,3)
        assert window.tabs.currentIndex() == 0
        assert "图传、状态显示和键鼠功能不受影响" in window.banner.text()
        window.config.map_calibrated = True
        window.map_clicked(2,3)
        assert window.tabs.currentIndex() == 1
        assert '"map_x": 2.0' in window.editor.toPlainText()
    finally:
        window.close()
        app.processEvents()


def test_demo_cannot_connect_official_server():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Config(), demo=True)
    try:
        window.toggle_connection()
        assert window.transport is None
        assert "本地演示只连接 127.0.0.1" in window.banner.text()
    finally:
        window.close()
        app.processEvents()
